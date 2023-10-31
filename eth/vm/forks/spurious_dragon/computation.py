from eth_hash.auto import (
    keccak,
)
from eth_utils import (
    encode_hex,
)

from eth import (
    constants,
)
from eth.abc import (
    ComputationAPI,
    MessageAPI,
    StateAPI,
    TransactionContextAPI,
)
from eth.exceptions import (
    OutOfGas,
    VMError,
)
from eth.vm.forks.homestead.computation import (
    HomesteadComputation,
)

from ..spurious_dragon.constants import (
    EIP170_CODE_SIZE_LIMIT,
)
from .opcodes import (
    SPURIOUS_DRAGON_OPCODES,
)


class SpuriousDragonComputation(HomesteadComputation):
    """
    A class for all execution *message* computations in the ``SpuriousDragon`` fork.
    Inherits from
    :class:`~eth.vm.forks.homestead.computation.HomesteadComputation`
    """

    # Override
    opcodes = SPURIOUS_DRAGON_OPCODES

    # 方法执行创建合约的消息操作，验证消息的有效性，创建合约并将合约字节码写入区块链中，同时扣除相应的燃气。
    # 如果在合约创建或验证合约字节码过程中出现错误，会回滚到之前的状态。这个方法是以太坊虚拟机中合约创建的核心部分。
    @classmethod
    def apply_create_message(
        cls,
        state: StateAPI,
        message: MessageAPI,
        transaction_context: TransactionContextAPI,
    ) -> ComputationAPI:
        # 创建了当前状态的快照。在执行合约创建之前，会保存当前状态的快照，以便在出现错误时能够回滚到原始状态。
        snapshot = state.snapshot()

        # 增加了消息发送者（创建合约的地址）的 nonce。这是根据 EIP-161 执行的一个步骤。
        state.increment_nonce(message.storage_address)

        # 用于验证创建合约消息的有效性
        cls.validate_create_message(message)
        # 执行实际的合约创建操作，并返回 Computation 对象
        computation = cls.apply_message(state, message, transaction_context)

        # 如果合约执行过程中发生错误（is_error 为真），则会回滚到之前的状态快照，
        # 并返回相应的 Computation 对象
        if computation.is_error:
            state.revert(snapshot)
            return computation
        else:
            contract_code = computation.output
            # 如果有合约字节码（即合约创建成功），执行以下步骤
            if contract_code:
                try:
                    # 验证合约字节码的有效性，确保它是有效的 EVM 字节码
                    cls.validate_contract_code(contract_code)

                    # 计算将合约字节码写入区块链的燃气成本。
                    # 燃气成本是基于字节码的长度和 GAS_CODEDEPOSIT 常量来计算的
                    contract_code_gas_cost = (
                        len(contract_code) * constants.GAS_CODEDEPOSIT
                    )
                    # 扣除燃气成本，表示写入合约字节码的燃气消耗
                    computation.consume_gas(
                        contract_code_gas_cost,
                        reason="Write contract code for CREATE",
                    )
                except VMError as err:
                    # Different from Frontier: reverts state on gas failure while
                    # writing contract code.
                    computation.error = err
                    state.revert(snapshot)
                    cls.logger.debug2(f"VMError setting contract code: {err}")
                else:
                    if cls.logger:
                        cls.logger.debug2(
                            "SETTING CODE: %s -> length: %s | hash: %s",
                            encode_hex(message.storage_address),
                            len(contract_code),
                            encode_hex(keccak(contract_code)),
                        )
                    # 将合约字节码写入以太坊区块链中的指定地址
                    state.set_code(message.storage_address, contract_code)
                    # 提交状态快照，将新状态永久保存在区块链中
                    state.commit(snapshot)
            else:
                state.commit(snapshot)
            return computation

    @classmethod
    def validate_create_message(cls, message: MessageAPI) -> None:
        # this method does not become relevant until the Shanghai hard fork
        """
        Class method for validating a create message.
        """
        pass

    @classmethod
    def validate_contract_code(cls, contract_code: bytes) -> None:
        if len(contract_code) > EIP170_CODE_SIZE_LIMIT:
            raise OutOfGas(
                f"Contract code size exceeds EIP170 limit of {EIP170_CODE_SIZE_LIMIT}."
                f"  Got code of size: {len(contract_code)}"
            )
