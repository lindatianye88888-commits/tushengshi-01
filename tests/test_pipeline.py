import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from shapely.geometry import box, LineString, mapping

from workflow.catalog import read_json
from workflow.compiler import prepare
from workflow.engine import execute
from workflow.integrity import inspect_run, review_run, frozen_input
from workflow.task import new_task, add_instance, confirm, parameter, effective_parameters, is_confirmed, write_json
from workflow.project import circle_project
from workflow.analysis import analyze
from workflow.spatial import context, load_dataset, file_hash
from workflow.providers.overture import contains_bbox


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.task=new_task("合成基准项目")
        self.task["is_example"]=True
        self.task["output_formats"]=["svg"]
        self.task["project"].update(source_crs="EPSG:32648",analysis_crs="EPSG:32648",
            boundary=mapping(box(500000,3400000,500100,3400100)))
        self.ctx=context(self.task["project"])
        self.path=self.root/"task.json"

    def resource(self,rid,features):
        data=self.root/f"{rid}.geojson"
        write_json(data,{"type":"FeatureCollection","features":[{"type":"Feature","id":fid,"geometry":mapping(geom),"properties":props} for fid,geom,props in features]})
        self.task["datasets"][rid]={"path":data.name,"source":"合成测试夹具","acquired_at":"2026-10-25",
            "data_period":"synthetic","crs":"EPSG:32648","units":"metres",
            "coverage":self.task["project"]["boundary"],"completeness":"complete","mapping_completeness":"verified_complete","estimated_fields":[]}
        return data

    def fabric(self):
        self.resource("buildings",[("b",box(500010,3400010,500060,3400060),{})])
        item=add_instance(self.task,"fabric");confirm(self.task);write_json(self.path,self.task)
        return item

    def test_utm_and_circular_scope_are_city_independent(self):
        north=circle_project({"name":"另一个项目"},2.3,48.8,1000)
        south=circle_project({"name":"南半球"},151.2,-33.8,1000)
        self.assertEqual(north["analysis_crs"],"EPSG:32631")
        self.assertEqual(south["analysis_crs"],"EPSG:32756")
        self.assertTrue(context(north).scopes["acquisition"].covers(context(north).scopes["analysis"]))
        self.assertTrue(contains_bbox([0,0,4,4],[1,1,2,2]))
        self.assertFalse(contains_bbox([0,0,1,1],[0,0,2,2]))

    def test_group_parameter_changes_only_compatible_diagrams(self):
        road=add_instance(self.task,"roads");fabric=add_instance(self.task,"fabric")
        self.task["group_tasks"]=[{"group_id":"base_map","shared_parameters":{"classification_property":parameter("road_class")}}]
        confirm(self.task)
        self.assertNotIn("classification_property",effective_parameters(self.task,fabric))
        self.task["group_tasks"][0]["shared_parameters"]["classification_property"]=parameter("highway")
        self.assertFalse(is_confirmed(self.task,road));self.assertTrue(is_confirmed(self.task,fabric))

    def test_unimplemented_diagram_does_not_poison_shared_resources(self):
        add_instance(self.task,"combined")
        add_instance(self.task,"roads")
        self.resource("roads",[("r",LineString([(500000,3400000),(500100,3400100)]),{"highway":"primary"})])
        confirm(self.task);write_json(self.path,self.task)
        plan=prepare(self.path)[1]
        self.assertTrue(plan["instances"][0]["blockers"])
        self.assertEqual(plan["instances"][1]["blockers"],[])

    def test_empty_data_needs_explicit_source_verification(self):
        self.resource("buildings",[])
        with self.assertRaisesRegex(ValueError,"空资料"):
            load_dataset("buildings",self.task["datasets"]["buildings"],self.root,self.ctx)
        self.task["datasets"]["buildings"]["verified_empty"]=True
        data=load_dataset("buildings",self.task["datasets"]["buildings"],self.root,self.ctx)
        self.assertEqual(analyze("fabric",{},data,self.ctx)["total_area_m2"],0)

    def test_excluded_parcel_does_not_cut_holes_in_kept_parcels(self):
        self.resource("landuse",[("large",box(500000,3400000,500100,3400100),{"landuse":"residential"}),
                                 ("small",box(500010,3400010,500030,3400030),{"landuse":"school"})])
        data=load_dataset("landuse",self.task["datasets"]["landuse"],self.root,self.ctx)
        result=analyze("landuse",{"classification_property":"landuse","class_mapping":{},"exclude_ids":["small"],"overlap_policy":"smallest_first"},data,self.ctx)
        self.assertAlmostEqual(result["total_area_m2"],10000)

    def test_water_priority_partitions_overlapping_park(self):
        self.resource("green_water",[("g",box(500000,3400000,500100,3400100),{"kind":"green"}),
                                     ("w",box(500010,3400010,500030,3400030),{"kind":"water"})])
        data=load_dataset("green_water",self.task["datasets"]["green_water"],self.root,self.ctx)
        result=analyze("gw",{"classification_property":"kind","class_mapping":{},"overlap_policy":"water_priority","water_classes":["water"]},data,self.ctx)
        self.assertEqual({row["category"]:row["value"] for row in result["table"]},{"green":9600,"water":400})

    def test_render_label_filter_uses_raw_road_type(self):
        self.resource("roads",[("p",LineString([(500000,3400000),(500100,3400000)]),{"highway":"footway","map_class":"步行与非机动车"})])
        data=load_dataset("roads",self.task["datasets"]["roads"],self.root,self.ctx)
        result=analyze("roads",{"classification_property":"map_class","class_mapping":{},"include_paths":False},data,self.ctx)
        self.assertEqual(result["total_length_m"],0)

    def test_full_lifecycle_freezes_inputs_and_survives_input_edits(self):
        self.fabric()
        run,manifest=execute(self.path,self.root/"runs")
        self.assertEqual(manifest["status"],"needs_review")
        self.assertTrue(inspect_run(run)["passed"])
        resource=manifest["input_resources"]["buildings"]
        frozen=frozen_input(run,resource)
        before=frozen.read_bytes()
        (self.root/"buildings.geojson").write_text("changed after the run",encoding="utf-8")
        self.assertEqual(frozen.read_bytes(),before)
        self.assertTrue(inspect_run(run)["passed"])
        review=review_run(run,"test-reviewer","accept","合成基准数值与SVG图框已核对。")
        self.assertEqual(review["status"],"complete")
        from workflow.delivery import bundle
        packet=self.root/"bundle.zip";bundle(run,packet)
        with zipfile.ZipFile(packet) as archive:
            portable=json.loads(archive.read("task.json"))
            expected=portable["datasets"]["buildings"]["path"]
            self.assertEqual(hashlib.sha256(archive.read(expected)).hexdigest(),resource["sha256"])
            self.assertIsNone(archive.testzip())

    def test_style_and_title_changes_reuse_analysis_but_tampering_blocks_review(self):
        self.fabric()
        first,m1=execute(self.path,self.root/"runs")
        self.task["shared_style"]["palette"]="monochrome"
        self.task["project"]["name"]="新标题"
        write_json(self.path,self.task)
        second,m2=execute(self.path,self.root/"runs")
        self.assertTrue(m2["instances"][0]["analysis_cache_hit"])
        self.assertEqual(m1["instances"][0]["analysis_key"],m2["instances"][0]["analysis_key"])
        svg=second/m2["instances"][0]["files"][0]["path"]
        text=svg.read_text(encoding="utf-8")
        self.assertNotIn("万象城",text);self.assertNotIn("Qian Shi",text)
        svg.write_text(text+"tampered",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"完整性"):
            review_run(second,"test","accept","不能接受已修改文件。")

    def test_repaint_requires_no_live_input_files(self):
        self.fabric();run,manifest=execute(self.path,self.root/"runs")
        (self.root/"buildings.geojson").unlink()
        from workflow.repaint import repaint
        target,updated=repaint(run,{"palette":"monochrome"},self.root/"redraw")
        self.assertEqual(updated["instances"][0]["analysis_file"]["sha256"],manifest["instances"][0]["analysis_file"]["sha256"])
        self.assertTrue(inspect_run(target)["passed"])


if __name__=="__main__": unittest.main()
