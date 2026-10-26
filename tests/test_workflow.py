import copy
import unittest

from shapely.geometry import box, LineString, mapping
from workflow.analysis import analyze
from workflow.spatial import context
from workflow.task import new_task, add_instance, confirm, requirement_signature, effective_parameters, parameter, active_cards


class WorkflowContracts(unittest.TestCase):
    def setUp(self):
        self.task = new_task("边界基准")
        self.task["project"].update(source_crs="EPSG:32648", analysis_crs="EPSG:32648",
                                    boundary=mapping(box(500000, 3400000, 500100, 3400100)))
        self.ctx = context(self.task["project"])

    def dataset(self, features):
        return {"features": features, "sha256": "fixture", "metadata": {}}

    def test_building_union_and_separate_display_scope(self):
        self.task["project"]["scopes"]["display"] = mapping(box(500000, 3400000, 500025, 3400025))
        ctx = context(self.task["project"])
        features = [{"id": "a", "geometry": box(500000, 3400000, 500050, 3400050), "properties": {}},
                    {"id": "b", "geometry": box(500025, 3400000, 500075, 3400050), "properties": {}}]
        result = analyze("fabric", {}, self.dataset(features), ctx)
        self.assertAlmostEqual(result["total_area_m2"], 3750)
        self.assertAlmostEqual(result["coverage_percent"], 37.5)
        self.assertEqual(result["analysis_feature_count"], 2)
        self.assertEqual(result["display_feature_count"], 1)  # A boundary touch has no polygon area.

    def test_road_dedup_preserves_grade_separation(self):
        line = LineString([(500000, 3400000), (500100, 3400000)])
        features = [{"id": str(n), "geometry": line, "properties": {"highway": "primary", "layer": level}}
                    for n, level in enumerate(["0", "0", "1"])]
        result = analyze("roads", {"classification_property": "highway", "class_mapping": {},
                                   "include_paths": True}, self.dataset(features), self.ctx)
        self.assertAlmostEqual(result["total_length_m"], 200)

    def test_nested_landuse_is_partitioned_without_double_counting(self):
        features = [{"id": "a", "geometry": box(500000,3400000,500100,3400100), "properties": {"landuse":"residential"}},
                    {"id": "b", "geometry": box(500020,3400020,500040,3400040), "properties": {"landuse":"school"}}]
        params = {"classification_property":"landuse", "class_mapping":{}, "overlap_policy":"smallest_first"}
        result = analyze("landuse", params, self.dataset(features), self.ctx)
        table = {r["category"]: r["value"] for r in result["table"]}
        self.assertEqual(table, {"residential":9600, "school":400})
        self.assertAlmostEqual(result["coverage_percent"], 100)
        params["overlap_policy"] = "reject"
        with self.assertRaisesRegex(ValueError, "重叠"):
            analyze("landuse", params, self.dataset(features), self.ctx)

    def test_candidates_do_not_activate_delivery_groups(self):
        add_instance(self.task,"roads")
        add_instance(self.task,"access",proposed=True,reason="待用户采纳")
        self.assertEqual([g["group_id"] for g in active_cards(self.task)], ["base_map"])
        add_instance(self.task,"roads")
        self.assertEqual(len(self.task["diagrams"]),2)

    def test_confirmation_detects_file_edits_but_allows_style_changes(self):
        item = add_instance(self.task,"roads")
        confirm(self.task)
        before = item["confirmed_signature"]
        self.task["shared_style"]["palette"]="monochrome"
        self.assertEqual(before,requirement_signature(self.task,item))
        item["parameters"]["include_paths"]=parameter(False)
        self.assertNotEqual(before,requirement_signature(self.task,item))

    def test_degrees_rejected_for_area_statistics(self):
        self.task["project"]["analysis_crs"]="EPSG:4326"
        with self.assertRaisesRegex(ValueError,"米"):
            context(self.task["project"])


if __name__=="__main__":
    unittest.main()
