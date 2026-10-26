import copy
import math
from pathlib import Path
import tempfile
import unittest
import zipfile

from shapely.geometry import box, LineString, Polygon, mapping, shape

from workflow.analysis import analyze
from workflow.catalog import DEFAULTS, read_json
from workflow.delivery import preview, bundle
from workflow.engine import execute
from workflow.integrity import inspect_run
from workflow.repaint import repaint
from workflow.spatial import context
from workflow.task import add_instance, confirm, new_task, write_json


class SpatialStatistics(unittest.TestCase):
    def setUp(self):
        self.task = new_task("空间统计算例")
        self.task["project"].update(source_crs="EPSG:32648", analysis_crs="EPSG:32648",
                                    boundary=mapping(box(500000, 3400000, 500100, 3400100)))
        self.ctx = context(self.task["project"])
        self.ctx.true_north = (0, 1)  # Known mathematical north for directional fixtures.

    def feature(self, fid, geom, **properties):
        return {"id": fid, "geometry": geom, "properties": properties}

    def compute(self, did, features, **parameters):
        return analyze(did, {**DEFAULTS[did], **parameters},
                       {"features": features, "sha256": "fixture", "metadata": {}}, self.ctx)

    def test_density_unions_overlap_and_uses_clipped_edge_denominator(self):
        self.task["project"]["boundary"] = mapping(box(500000, 3400000, 500075, 3400100))
        self.ctx = context(self.task["project"])
        features = [self.feature("a", box(500000, 3400000, 500075, 3400100)),
                    self.feature("b", box(500000, 3400000, 500025, 3400100))]
        result = self.compute("density", features, grid_size_m=50)
        self.assertEqual(result["total_area_m2"], 7500)
        self.assertEqual(result["coverage_percent"], 100)
        self.assertEqual(len(result["detail_table"]), 4)
        self.assertEqual(min(row["effective_area_m2"] for row in result["detail_table"]), 1250)
        self.assertTrue(all(row["coverage_percent"] == 100 for row in result["detail_table"]))
        self.assertAlmostEqual(sum(row["building_area_m2"] for row in result["detail_table"]), 7500)
        self.assertAlmostEqual(sum(row["effective_area_m2"] for row in result["detail_table"]), 7500)

    def test_density_empty_cells_and_explicit_exclusions(self):
        features = [self.feature("a", box(500000, 3400000, 500025, 3400100))]
        result = self.compute("density", features, grid_size_m=50)
        self.assertEqual(result["coverage_percent"], 25)
        self.assertEqual(sum(row["coverage_percent"] == 0 for row in result["detail_table"]), 2)
        self.assertEqual(self.compute("density", features, grid_size_m=50, exclude_ids=["a"])["total_area_m2"], 0)
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.compute("density", features, exclude_ids=["missing"])

    def test_rose_splits_bends_deduplicates_lines_and_preserves_levels(self):
        bend = LineString([(500000, 3400000), (500100, 3400000), (500100, 3400100)])
        north = LineString([(500100, 3400000), (500100, 3400100)])
        features = [self.feature("a", bend), self.feature("dup", LineString(list(bend.coords)[::-1])),
                    self.feature("upper", north, layer="1"), self.feature("path", LineString([(500000, 3400000), (500000, 3400100)]), highway="footway")]
        result = self.compute("orientrose", features, bin_count=4)
        self.assertEqual(result["total_length_m"], 300)
        self.assertEqual([row["value"] for row in result["table"]], [200, 0, 100, 0])
        self.assertEqual(self.compute("orientrose", features, bin_count=4, include_paths=True)["total_length_m"], 400)

    def test_rose_undirected_result_does_not_depend_on_vertex_order(self):
        line = LineString([(500000, 3400050), (500100, 3400050)])
        forward = self.compute("orientrose", [self.feature("a", line)], fold_opposite=False, bin_count=8)
        backward = self.compute("orientrose", [self.feature("b", LineString(list(line.coords)[::-1]))], fold_opposite=False, bin_count=8)
        self.assertEqual(forward["table"], backward["table"])
        self.assertEqual([row["value"] for row in forward["table"]], [0, 0, 50, 0, 0, 0, 50, 0])

    def test_rose_measures_relative_to_true_north(self):
        self.ctx.true_north = (1, 0)
        result = self.compute("orientrose", [self.feature("east", LineString([(500000, 3400050), (500100, 3400050)]))], bin_count=4)
        self.assertEqual(result["table"][0]["value"], 100)

    def test_typology_preserves_holes_deduplicates_and_filters_partial_buildings(self):
        outer = box(500000, 3400000, 500100, 3400100)
        hole = box(500020, 3400020, 500040, 3400040)
        courtyard = Polygon(outer.exterior.coords, [hole.exterior.coords])
        features = [self.feature("a", courtyard), self.feature("duplicate", courtyard.reverse()),
                    self.feature("b", box(500050, 3400050, 500060, 3400060)),
                    self.feature("outside", box(500090, 3400090, 500110, 3400110))]
        result = self.compute("typology", features, max_buildings=2)
        self.assertEqual(result["analysis_feature_ids"], ["a", "b"])
        self.assertEqual(result["eligible_feature_count"], 2)
        self.assertEqual(result["total_area_m2"], 9700)
        self.assertEqual(len(shape(result["display_features"][0]["geometry"]).interiors), 1)
        self.assertTrue(all(0 < row["compactness"] <= 1 for row in result["detail_table"]))
        self.assertIsNone(result["coverage_percent"])

    def test_unsupported_modes_and_invalid_parameters_are_rejected(self):
        for did, parameters in [("density", {"mode": "far"}), ("density", {"grid_size_m": math.nan}),
                                ("orientrose", {"bin_count": True}), ("typology", {"same_scale": False})]:
            with self.subTest(did=did, parameters=parameters):
                with self.assertRaises(ValueError): self.compute(did, [], **parameters)

    def test_verified_empty_sources_produce_explicit_empty_results(self):
        self.assertEqual(self.compute("density", [], grid_size_m=50)["coverage_percent"], 0)
        self.assertIsNone(self.compute("orientrose", [])["dominant_orientation_degrees"])
        self.assertEqual(self.compute("typology", [])["eligible_feature_count"], 0)

    def test_three_diagrams_export_details_repaint_and_bundle_without_live_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = copy.deepcopy(self.task);task["is_example"] = True
            task["output_formats"] = ["svg", "png"]
            fixtures = {"buildings": [self.feature("b", box(500010, 3400010, 500070, 3400070))],
                        "roads": [self.feature("r", LineString([(500000, 3400050), (500100, 3400050)]), highway="primary")]}
            for rid, features in fixtures.items():
                path = root/f"{rid}.geojson"
                write_json(path, {"type": "FeatureCollection", "features": [{"type": "Feature", **f, "geometry": mapping(f["geometry"])} for f in features]})
                task["datasets"][rid] = {"path": path.name, "source": "synthetic", "acquired_at": "2026-10-25", "data_period": "synthetic",
                                         "crs": "EPSG:32648", "units": "metres", "coverage": task["project"]["boundary"],
                                         "completeness": "complete", "mapping_completeness": "verified_complete", "estimated_fields": []}
            for did in ("density", "orientrose", "typology"): add_instance(task, did)
            confirm(task);write_json(root/"task.json", task)
            run, manifest = execute(root/"task.json", root/"runs")
            self.assertEqual(manifest["status"], "needs_review")
            self.assertEqual(manifest["generated_file_count"], 6)
            self.assertTrue(inspect_run(run)["passed"])
            self.assertTrue(preview(run).exists())
            for rid in fixtures: (root/f"{rid}.geojson").unlink()
            target, updated = repaint(run, {"palette": "monochrome"})
            self.assertTrue(inspect_run(target)["passed"])
            for old, new in zip(manifest["instances"], updated["instances"]):
                self.assertEqual(old["details_file"]["sha256"], new["details_file"]["sha256"])
                self.assertEqual(old["analysis_file"]["sha256"], new["analysis_file"]["sha256"])
            bundle(target, root/"packet.zip")
            with zipfile.ZipFile(root/"packet.zip") as packet:
                self.assertEqual(len([name for name in packet.namelist() if name.endswith("/details.csv")]), 3)
            detail = target/updated["instances"][0]["details_file"]["path"]
            detail.write_text("tampered", encoding="utf-8")
            self.assertFalse(inspect_run(target)["passed"])


if __name__ == "__main__": unittest.main()
