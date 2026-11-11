"""Offline callback regression tests; no CTP connection or native API required.

Run from the repository root with ``python -m unittest discover -s tests -v``
inside a VeighNa environment. Only the two native API classes are replaced;
constants, the gateway callback and VeighNa OrderData/Status are real.
"""

import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from vnpy.trader.constant import Exchange, Status


class OrderStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Importing vnpy.trader.utility initializes .vntrader; keep that isolated.
        with TemporaryDirectory() as directory:
            Path(directory, ".vntrader").mkdir()
            previous = Path.cwd()
            md = ModuleType("vnpy_ctptest.api.vnctpmd")
            td = ModuleType("vnpy_ctptest.api.vnctptd")
            md.MdApi = object
            td.TdApi = object
            try:
                os.chdir(directory)
                with patch.dict("sys.modules", {
                    md.__name__: md,
                    td.__name__: td,
                }):
                    cls.gateway_module = importlib.import_module(
                        "vnpy_ctptest.gateway.ctptest_gateway"
                    )
                    cls.constants = importlib.import_module("vnpy_ctptest.api.ctp_constant")
            finally:
                os.chdir(previous)

    def setUp(self) -> None:
        self.orders = []
        self.api = SimpleNamespace(
            contract_inited=True,
            order_data=[],
            gateway=SimpleNamespace(on_order=self.orders.append),
            gateway_name="CTPTEST",
            sysid_orderid_map={},
        )
        self.contracts = patch.dict(self.gateway_module.symbol_contract_map, {
            "rb2612": SimpleNamespace(exchange=Exchange.SHFE)
        }, clear=True)
        self.contracts.start()
        self.addCleanup(self.contracts.stop)
        c = self.constants
        self.data = {
            "InstrumentID": "rb2612", "FrontID": 1, "SessionID": 2,
            "OrderRef": "3", "OrderSysID": "test-system-id",
            "InsertDate": "20261111", "InsertTime": "09:01:02",
            "OrderPriceType": c.THOST_FTDC_OPT_LimitPrice,
            "TimeCondition": c.THOST_FTDC_TC_GFD,
            "VolumeCondition": c.THOST_FTDC_VC_AV,
            "Direction": c.THOST_FTDC_D_Buy,
            "CombOffsetFlag": c.THOST_FTDC_OF_Open,
            "LimitPrice": 3500, "VolumeTotalOriginal": 2, "VolumeTraded": 0,
            "OrderStatus": c.THOST_FTDC_OST_Canceled,
            "OrderSubmitStatus": c.THOST_FTDC_OSS_Accepted,
            "StatusMsg": "",
        }

    def emit(self) -> None:
        self.gateway_module.CtptestTdApi.onRtnOrder(self.api, self.data)

    def test_insert_rejection_is_not_cancellation(self) -> None:
        self.data["OrderSubmitStatus"] = self.constants.THOST_FTDC_OSS_InsertRejected
        self.emit()
        self.assertEqual(self.orders[0].status, Status.REJECTED)
        self.assertEqual(self.orders[0].orderid, "1_2_3")
        self.assertEqual(self.api.sysid_orderid_map["test-system-id"], "1_2_3")

    def test_insert_rejection_with_unknown_order_status(self) -> None:
        self.data["OrderSubmitStatus"] = self.constants.THOST_FTDC_OSS_InsertRejected
        self.data["OrderStatus"] = self.constants.THOST_FTDC_OST_Unknown
        self.emit()
        self.assertEqual(self.orders[0].status, Status.REJECTED)

    def test_normal_cancellation_does_not_depend_on_message(self) -> None:
        for message in ("已撤单", "Canceled", "", "资金不足"):
            with self.subTest(message=message):
                self.data["StatusMsg"] = message
                self.emit()
                self.assertEqual(self.orders[-1].status, Status.CANCELLED)

    def test_ioc_cancellation_is_not_rejection(self) -> None:
        c = self.constants
        self.data["TimeCondition"] = c.THOST_FTDC_TC_IOC
        for condition in (c.THOST_FTDC_VC_AV, c.THOST_FTDC_VC_CV):
            with self.subTest(condition=condition):
                self.data["VolumeCondition"] = condition
                self.emit()
                self.assertEqual(self.orders[-1].status, Status.CANCELLED)

    def test_cancel_and_modify_rejections_preserve_live_order(self) -> None:
        c = self.constants
        for submission in (c.THOST_FTDC_OSS_CancelRejected, c.THOST_FTDC_OSS_ModifyRejected):
            for status, expected, traded in (
                (c.THOST_FTDC_OST_NoTradeQueueing, Status.NOTTRADED, 0),
                (c.THOST_FTDC_OST_PartTradedQueueing, Status.PARTTRADED, 1),
            ):
                with self.subTest(submission=submission, status=status):
                    self.data.update(OrderSubmitStatus=submission, OrderStatus=status, VolumeTraded=traded)
                    self.emit()
                    self.assertEqual(self.orders[-1].status, expected)
                    self.assertEqual(self.orders[-1].traded, traded)

    def test_normal_execution_states_are_unchanged(self) -> None:
        c = self.constants
        for status, expected in (
            (c.THOST_FTDC_OST_NoTradeQueueing, Status.NOTTRADED),
            (c.THOST_FTDC_OST_PartTradedQueueing, Status.PARTTRADED),
            (c.THOST_FTDC_OST_AllTraded, Status.ALLTRADED),
        ):
            with self.subTest(status=status):
                self.data["OrderStatus"] = status
                self.emit()
                self.assertEqual(self.orders[-1].status, expected)

    def test_buffered_rejection_is_classified_after_contract_initialization(self) -> None:
        self.api.contract_inited = False
        self.data["OrderSubmitStatus"] = self.constants.THOST_FTDC_OSS_InsertRejected
        self.emit()
        self.assertEqual(self.orders, [])
        self.assertEqual(self.api.order_data, [self.data])
        self.api.contract_inited = True
        self.gateway_module.CtptestTdApi.onRtnOrder(self.api, self.api.order_data.pop())
        self.assertEqual(self.orders[0].status, Status.REJECTED)


if __name__ == "__main__":
    unittest.main()
