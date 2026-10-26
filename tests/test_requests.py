from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shapely.geometry import box
from workflow.catalog import read_json
from workflow.ingest import fetch_task
from workflow.request import apply_request, start_request
from workflow.task import new_task, add_instance, write_json
from workflow.project import circle_project


class FakeTable:
    def __init__(self,rows): self.rows=rows
    def to_pylist(self): return self.rows


class DesktopRequests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.path=self.root/"request.json"

    def request(self):
        request={"project":{"name":"跨端示例","center":{"lon":104.1126,"lat":30.6507,"crs":"EPSG:4326","label":"校核点"},"radius_m":1000},
                 "diagrams":["fabric"],"formats":["svg"],"data":{"mode":"existing"},"requirements_confirmed":False}
        write_json(self.path,request);return request

    def test_file_only_request_creates_scope_and_does_not_confirm(self):
        self.request();task_path,request=apply_request(self.path)
        task=read_json(task_path)
        self.assertEqual(task["project"]["analysis_crs"],"EPSG:32648")
        self.assertFalse(task["diagrams"][0]["confirmed"])
        result=start_request(self.path)
        self.assertEqual(result["status"],"needs_input")
        self.assertTrue((self.root/"request.result.json").exists())

    def test_request_updates_merge_selections_and_preserve_rejections(self):
        request=self.request();task_path,_=apply_request(self.path)
        task=read_json(task_path);task["rejected_recommendations"]=["access"];write_json(task_path,task)
        request["diagrams"]=["roads"];request["recommendations"]=[{"id":"access","reason":"已拒绝的推荐"}]
        write_json(self.path,request);apply_request(self.path)
        task=read_json(task_path)
        self.assertEqual({i["diagram_id"] for i in task["diagrams"]},{"fabric","roads"})

    def test_declared_confirmation_requires_authorization_record(self):
        request=self.request();request["requirements_confirmed"]=True;write_json(self.path,request)
        with self.assertRaisesRegex(ValueError,"authorization_note"): apply_request(self.path)

    def test_generic_ingest_attaches_all_four_sources_and_specific_attribution(self):
        task=new_task("跨城市取数基准")
        task["project"]=circle_project(task["project"],104.1126,30.6507,1000)
        for did in ("fabric","roads","landuse","gw"): add_instance(task,did)
        task_path=self.root/"task.json";write_json(task_path,task)
        polygon=box(104.112,30.650,104.113,30.651).wkb
        def source(cache,kind,bbox,**kwargs):
            row={"id":kind,"geometry":polygon,"names":None,"class":"park","subtype":None,
                 "sources":[{"dataset":"doi:10.5281/zenodo.8174931","property":""}] if kind=="building" else []}
            if kind=="segment":
                from shapely.geometry import LineString
                row.update(geometry=LineString([(104.112,30.650),(104.113,30.651)]).wkb,subtype="road",**{"class":"primary"})
            return FakeTable([row]),{"release":"2026-10-21.0","bbox":list(bbox)}
        with patch("workflow.ingest.extract",side_effect=source):
            result=fetch_task(task_path,self.root/"cache",offline=True)
        self.assertEqual(set(result["updated_resources"]),{"buildings","roads","landuse","green_water"})
        self.assertEqual(result["errors"],{})
        task=read_json(task_path)
        self.assertIn("Qian Shi",task["datasets"]["buildings"]["attributions"][-1])
        self.assertNotIn("Qian Shi",str(task["datasets"]["roads"]["attributions"]))
        self.assertTrue(all(not i["confirmed"] for i in task["diagrams"]))
        from workflow.compiler import prepare
        self.assertTrue(all(i["data_status"]=="ready" for i in prepare(task_path)[1]["instances"]))


if __name__=="__main__": unittest.main()
