"""贴源表状态流转与明细上游准入的回归测试。

直接从服务层驱动，覆盖：确认上线单向推进、上游准入校验、并发重复登记、
事务回滚、下线回写与跨入口一致。运行：.venv/bin/python -m unittest discover tests
"""
from __future__ import annotations

import threading
import unittest

from app.services import upstream
from app.services.dwd import DwdService
from app.services.ods import OdsService
from app.store import store
from app.seed import SEED_ROWS


class UpstreamFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
        store._relations = []
        store._commit_seq = 0
        self.ods = OdsService()
        self.dwd = DwdService()

    def _register_pay(self) -> int:
        entry, _ = self.ods.create_entry({"表编号": "ODS_PAY", "表名称": "支付贴源", "来源系统": "PAY_CORE"})
        assert entry is not None
        return int(entry["id"])

    def test_confirm_online_requires_approval(self) -> None:
        entry_id = self._register_pay()
        entry, message = self.ods.run_action(entry_id, "确认上线")
        self.assertIsNone(entry)
        self.assertIn("草稿", message)
        self.ods.run_action(entry_id, "提交建表审批")
        entry, _ = self.ods.run_action(entry_id, "确认上线")
        self.assertIsNotNone(entry)
        # 重复确认上线不再生效，状态保持已上线。
        again, message = self.ods.run_action(entry_id, "确认上线")
        self.assertIsNone(again)
        self.assertIn("重复", message)
        self.assertEqual(upstream.find_ods("ODS_PAY")["status"], "已上线")

    def test_upstream_requires_online_and_same_source(self) -> None:
        entry_id = self._register_pay()
        self.ods.run_action(entry_id, "提交建表审批")
        created, _, message = self.dwd.create_entry(
            {"表编号": "DWD_PAY_FACT", "表名称": "支付事实", "业务域": "PAY_CORE", "上游贴源表": "ODS_PAY"}
        )
        self.assertIsNone(created)
        self.assertIn("尚未上线", message)
        self.assertFalse(any(row.get("表编号") == "DWD_PAY_FACT" for row in store.rows("dwd")))

        self.ods.run_action(entry_id, "确认上线")
        created, _, message = self.dwd.create_entry(
            {"表编号": "DWD_PAY_FACT", "表名称": "支付事实", "业务域": "PAY_CORE", "上游贴源表": "ODS_PAY"}
        )
        self.assertIsNotNone(created)
        self.assertEqual(message, "")
        dwd_row = next(row for row in store.rows("dwd") if row["表编号"] == "DWD_PAY_FACT")
        self.assertEqual(dwd_row["上游贴源表"], "ODS_PAY")
        relation = upstream.relation_for(int(dwd_row["id"]))
        self.assertEqual(relation["准入结论"], upstream.ADMITTED)
        self.assertTrue(relation["生效"])
        self.assertEqual(relation["提交序号"], relation["生效序号"])

        mismatch, _, message = self.dwd.create_entry(
            {"表编号": "DWD_OTHER", "表名称": "别的域", "业务域": "OTHER", "上游贴源表": "ODS_PAY"}
        )
        self.assertIsNone(mismatch)
        self.assertIn("不一致", message)

    def test_duplicate_registration_effective_once(self) -> None:
        entry_id = self._register_pay()
        self.ods.run_action(entry_id, "提交建表审批")
        self.ods.run_action(entry_id, "确认上线")
        created, _, _ = self.dwd.create_entry(
            {"表编号": "DWD_PAY_FACT", "表名称": "支付事实", "业务域": "PAY_CORE", "上游贴源表": "ODS_PAY"}
        )
        dwd_id = int(created["id"])
        relation = upstream.relation_for(dwd_id)
        effective_seq = relation["生效序号"]

        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(self.dwd.save_upstream(dwd_id, {"上游贴源表": "ODS_PAY"}))
            )
            for _ in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len([rel for rel in store.relation_rows() if rel["dwd_id"] == dwd_id]), 1)
        relation = upstream.relation_for(dwd_id)
        self.assertEqual(relation["生效序号"], effective_seq)
        self.assertTrue(all("已生效" in message for _, message in results))
        audits = [row for row in store.rows("auditlog") if row["变更类型"] == "明细上游关系变更"]
        self.assertLessEqual(len(audits), 1)

    def test_offline_rewrites_conclusion_and_keeps_definition(self) -> None:
        entry_id = self._register_pay()
        self.ods.run_action(entry_id, "提交建表审批")
        self.ods.run_action(entry_id, "确认上线")
        created, _, _ = self.dwd.create_entry(
            {"表编号": "DWD_PAY_FACT", "表名称": "支付事实", "业务域": "PAY_CORE", "上游贴源表": "ODS_PAY"}
        )
        dwd_id = int(created["id"])
        self.ods.run_action(entry_id, "下线表")

        entry, relation = self.dwd.detail(dwd_id)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["表编号"], "DWD_PAY_FACT")
        self.assertEqual(relation["准入结论"], upstream.INACTIVE)
        self.assertEqual(relation["刷新结论"], upstream.REFRESH_BLOCKED)
        self.assertFalse(relation["生效"])
        # 重新登记同一条已下线关系会被拦下，历史定义仍然保留。
        blocked, message = self.dwd.save_upstream(dwd_id, {"上游贴源表": "ODS_PAY"})
        self.assertIsNone(blocked)
        self.assertIn("已下线", message)
        self.assertTrue(any(row["id"] == dwd_id for row in store.rows("dwd")))
        # 下线后不能再确认上线。
        again, message = self.ods.run_action(entry_id, "确认上线")
        self.assertIsNone(again)
        self.assertIn("已下线", message)

        audits = store.rows("auditlog")
        self.assertTrue(any(row["变更类型"] == "上游准入结论回写" for row in audits))

    def test_transaction_rolls_back_both_sides(self) -> None:
        entry_id = self._register_pay()
        self.ods.run_action(entry_id, "提交建表审批")
        self.ods.run_action(entry_id, "确认上线")
        created, _, _ = self.dwd.create_entry(
            {"表编号": "DWD_PAY_FACT", "表名称": "支付事实", "业务域": "PAY_CORE", "上游贴源表": "ODS_PAY"}
        )
        dwd_id = int(created["id"])
        before_relation = dict(upstream.relation_for(dwd_id))
        before_status = upstream.find_ods("ODS_PAY")["status"]
        with self.assertRaises(RuntimeError):
            with store.transaction():
                upstream.find_ods("ODS_PAY")["status"] = "已下线"
                upstream.relation_for(dwd_id)["准入结论"] = upstream.INACTIVE
                store.rows("auditlog").append({"id": 999, "status": "已记录"})
                raise RuntimeError("模拟写入中途失败")
        self.assertEqual(upstream.find_ods("ODS_PAY")["status"], before_status)
        self.assertEqual(upstream.relation_for(dwd_id)["准入结论"], before_relation["准入结论"])
        self.assertFalse(any(row.get("id") == 999 for row in store.rows("auditlog")))

    def test_list_and_detail_share_conclusion(self) -> None:
        entry_id = self._register_pay()
        self.ods.run_action(entry_id, "提交建表审批")
        self.ods.run_action(entry_id, "确认上线")
        created, _, _ = self.dwd.create_entry(
            {"表编号": "DWD_PAY_FACT", "表名称": "支付事实", "业务域": "PAY_CORE", "上游贴源表": "ODS_PAY"}
        )
        dwd_id = int(created["id"])
        listed = next(row for row in self.dwd.list_entries(size=50)[0] if row["id"] == dwd_id)
        detail, _ = self.dwd.detail(dwd_id)
        self.assertEqual(listed["上游准入结论"], detail["上游准入结论"])
        ods_entry, downstream = self.ods.detail(entry_id)
        self.assertEqual(downstream[0]["准入结论"], listed["上游准入结论"])
        self.assertEqual(ods_entry["下游明细数"], 1)

        self.ods.run_action(entry_id, "下线表")
        listed = next(row for row in self.dwd.list_entries(size=50)[0] if row["id"] == dwd_id)
        detail, _ = self.dwd.detail(dwd_id)
        ods_entry, downstream = self.ods.detail(entry_id)
        self.assertEqual(listed["上游准入结论"], upstream.INACTIVE)
        self.assertEqual(detail["上游准入结论"], upstream.INACTIVE)
        self.assertEqual(downstream[0]["准入结论"], upstream.INACTIVE)
        self.assertEqual(ods_entry["受影响明细数"], 1)


if __name__ == "__main__":
    unittest.main()
