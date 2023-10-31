from eth_hash.auto import (
    keccak,
)
from eth_utils import (
    encode_hex,
)

from eth import (
    precompiles,
)
from eth._utils.address import (
    force_bytes_to_address,
)
from eth.abc import (
    ComputationAPI,
    MessageAPI,
    StateAPI,
    TransactionContextAPI,
)
from eth.constants import (
    GAS_CODEDEPOSIT,
    STACK_DEPTH_LIMIT,
)
from eth.exceptions import (
    InsufficientFunds,
    OutOfGas,
    StackDepthLimit,
)
from eth.vm.computation import (
    BaseComputation,
)

from .opcodes import (
    FRONTIER_OPCODES,
)

FRONTIER_PRECOMPILES = {
    force_bytes_to_address(b"\x01"): precompiles.ecrecover,
    force_bytes_to_address(b"\x02"): precompiles.sha256,
    force_bytes_to_address(b"\x03"): precompiles.ripemd160,
    force_bytes_to_address(b"\x04"): precompiles.identity,
}


class FrontierComputation(BaseComputation):
    """
    A class for all execution message computations in the ``Frontier`` fork.
    Inherits from :class:`~eth.vm.computation.BaseComputation`
    """

    # Override
    opcodes = FRONTIER_OPCODES
    _precompiles = FRONTIER_PRECOMPILES  # type: ignore # https://github.com/python/mypy/issues/708 # noqa: E501

    # 处理消息的执行，包括转账操作和合约内部调用，确保执行消息操作时，余额、堆栈深度等都满足要求。
    # 在出现错误时，会回滚状态，以保持状态的一致性。
    # 这个方法是以太坊虚拟机中消息处理的核心部分。
    @classmethod
    def apply_message(
        cls,
        state: StateAPI,
        message: MessageAPI,
        transaction_context: TransactionContextAPI,
    ) -> ComputationAPI:
        # 创建了当前状态的快照
        snapshot = state.snapshot()

        # 检查消息的堆栈深度是否超出了堆栈深度限制。如果是，会引发 StackDepthLimit 异常。
        if message.depth > STACK_DEPTH_LIMIT:
            raise StackDepthLimit("Stack depth limit reached")

        # 如果消息有转账操作（普通转账、创建、调用合约花费余额）
        if message.should_transfer_value and message.value:
            # 获取消息发送者的余额
            sender_balance = state.get_balance(message.sender)

            # 如果消息发送者的余额小于消息中指定的价值，引发 InsufficientFunds 异常，表示余额不足以支付价值
            if sender_balance < message.value:
                raise InsufficientFunds(
                    f"Insufficient funds: {sender_balance} < {message.value}"
                )

            # 减少消息发送者的余额，扣除转账的价值
            state.delta_balance(message.sender, -1 * message.value)
            # 增加消息中指定的接收地址的余额，增加转账的价值
            state.delta_balance(message.storage_address, message.value)

            cls.logger.debug2(
                "TRANSFERRED: %s from %s -> %s",
                message.value,
                encode_hex(message.sender),
                encode_hex(message.storage_address),
            )
        # 标记接收地址对应的账户在本次交易中已经被访问过
        state.touch_account(message.storage_address)
        # 调用 apply_computation 方法，执行消息的计算，并返回 Computation 对象
        computation = cls.apply_computation(
            state,
            message,
            transaction_context,
        )

        if computation.is_error:
            state.revert(snapshot)
        else:
            state.commit(snapshot)

        return computation

    @classmethod
    def apply_create_message(
        cls,
        state: StateAPI,
        message: MessageAPI,
        transaction_context: TransactionContextAPI,
    ) -> ComputationAPI:
        computation = cls.apply_message(state, message, transaction_context)

        if computation.is_error:
            return computation
        else:
            contract_code = computation.output

            if contract_code:
                contract_code_gas_fee = len(contract_code) * GAS_CODEDEPOSIT
                try:
                    computation.consume_gas(
                        contract_code_gas_fee,
                        reason="Write contract code for CREATE",
                    )
                except OutOfGas:
                    computation.output = b""
                else:
                    cls.logger.debug2(
                        "SETTING CODE: %s -> length: %s | hash: %s",
                        encode_hex(message.storage_address),
                        len(contract_code),
                        encode_hex(keccak(contract_code)),
                    )
                    state.set_code(message.storage_address, contract_code)
            return computation
