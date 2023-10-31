from typing import (
    Any,
    Callable,
    Type,
    TypeVar,
)

from eth_utils import (
    ExtendedDebugLogger,
    get_extended_debug_logger,
)

from eth._utils.datatypes import (
    Configurable,
)
from eth.abc import (
    ComputationAPI,
    OpcodeAPI,
)

T = TypeVar("T")


class _FastOpcode(OpcodeAPI):
    __slots__ = ("logic_fn", "mnemonic", "gas_cost")

    def __init__(
            self, logic_fn: Callable[..., Any], mnemonic: str, gas_cost: int
    ) -> None:
        self.logic_fn = logic_fn
        self.mnemonic = mnemonic
        self.gas_cost = gas_cost

    # 操作码执行函数
    def __call__(self, computation: ComputationAPI) -> None:
        # 扣除操作码的燃气成本（gas cost），并传递操作码的助记符（mnemonic）
        computation.consume_gas(self.gas_cost, self.mnemonic)
        # 执行了操作码的逻辑。self.logic_fn 是在创建操作码时提供的逻辑函数，它会对 computation 进行操作，完成操作码的具体功能。
        return self.logic_fn(computation)

    @classmethod
    def as_opcode(
            cls: Type["_FastOpcode"],
            logic_fn: Callable[..., Any],
            mnemonic: str,
            gas_cost: int,
    ) -> OpcodeAPI:
        return cls(logic_fn, mnemonic, gas_cost)


class Opcode(Configurable, OpcodeAPI):
    mnemonic: str = None
    gas_cost: int = None

    def __init__(self) -> None:
        if self.mnemonic is None:
            raise TypeError(f"Opcode class {type(self)} missing opcode mnemonic")
        if self.gas_cost is None:
            raise TypeError(f"Opcode class {type(self)} missing opcode gas_cost")

    @property
    def logger(self) -> ExtendedDebugLogger:
        return get_extended_debug_logger(f"eth.vm.logic.{self.mnemonic}")

    @classmethod
    def as_opcode(
            cls: Type[T], logic_fn: Callable[..., Any], mnemonic: str, gas_cost: int
    ) -> OpcodeAPI:
        return _FastOpcode(logic_fn, mnemonic, gas_cost)


as_opcode = Opcode.as_opcode
