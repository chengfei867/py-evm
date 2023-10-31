from typing import (
    Type,
)

from eth_hash.auto import (
    keccak,
)
from eth_utils import (
    encode_hex,
)

from eth._utils.address import (
    generate_contract_address,
)
from eth.abc import (
    AccountDatabaseAPI,
    ComputationAPI,
    MessageAPI,
    SignedTransactionAPI,
    TransactionContextAPI,
    TransactionExecutorAPI,
)
from eth.constants import (
    CREATE_CONTRACT_ADDRESS,
)
from eth.db.account import (
    AccountDB,
)
from eth.exceptions import (
    ContractCreationCollision,
)
from eth.vm.message import (
    Message,
)
from eth.vm.state import (
    BaseState,
    BaseTransactionExecutor,
)

from .computation import (
    FrontierComputation,
)
from .constants import (
    MAX_REFUND_QUOTIENT,
    REFUND_SELFDESTRUCT,
)
from .transaction_context import (
    FrontierTransactionContext,
)
from .validation import (
    validate_frontier_transaction,
)


class FrontierTransactionExecutor(BaseTransactionExecutor):
    def validate_transaction(self, transaction: SignedTransactionAPI) -> None:
        # Validate the transaction
        transaction.validate()
        self.vm_state.validate_transaction(transaction)

    def build_evm_message(self, transaction: SignedTransactionAPI) -> MessageAPI:
        # Use vm_state.get_gas_price instead of transaction_context.gas_price so
        #   that we can run get_transaction_result (aka~ eth_call) and estimate_gas.
        #   Both work better if the GASPRICE opcode returns the original real price,
        #   but the sender's balance doesn't actually deduct the gas. This
        #   get_gas_price() will return 0 for eth_call, but
        #   transaction_context.gas_price will return
        #   the same value as the GASPRICE opcode.
        gas_fee = transaction.gas * self.vm_state.get_gas_price(transaction)

        # Buy Gas
        self.vm_state.delta_balance(transaction.sender, -1 * gas_fee)

        # Increment Nonce
        self.vm_state.increment_nonce(transaction.sender)

        # Setup VM Message
        message_gas = transaction.gas - transaction.intrinsic_gas

        if transaction.to == CREATE_CONTRACT_ADDRESS:
            contract_address = generate_contract_address(
                transaction.sender,
                self.vm_state.get_nonce(transaction.sender) - 1,
            )
            data = b""
            code = transaction.data
        else:
            contract_address = None
            data = transaction.data
            code = self.vm_state.get_code(transaction.to)

        self.vm_state.logger.debug2(
            (
                "TRANSACTION: sender: %s | to: %s | value: %s | gas: %s | "
                "gas-price: %s | s: %s | r: %s | y_parity: %s | data-hash: %s"
            ),
            encode_hex(transaction.sender),
            encode_hex(transaction.to),
            transaction.value,
            transaction.gas,
            transaction.gas_price,
            transaction.s,
            transaction.r,
            transaction.y_parity,
            encode_hex(keccak(transaction.data)),
        )

        message = Message(
            gas=message_gas,
            to=transaction.to,
            sender=transaction.sender,
            value=transaction.value,
            data=data,
            code=code,
            create_address=contract_address,
        )
        return message

    # 负责创建 EVM 执行的计算对象（Computation），并根据消息的类型（create 或 call）执行相应的计算。
    def build_computation(
        self, message: MessageAPI, transaction: SignedTransactionAPI
    ) -> ComputationAPI:
        # 此行代码用于获取与交易相关的事务上下文，该上下文包含有关交易的信息，如发送者、接收者和交易参数
        transaction_context = self.vm_state.get_transaction_context(transaction)
        # 检查消息类型是否是创建合约（create）。如果是 create 消息，那么需要执行合约的创建操作，否则执行普通消息调用
        if message.is_create:
            # 检查是否发生地址碰撞。它通过检查消息中的 storage_address 是否与现有合约地址（已有合约的地址）发生冲突来进行检查
            is_collision = self.vm_state.has_code_or_nonce(message.storage_address)

            # 如果存在地址碰撞，表示合约创建失败，因为合约地址已经被使用。
            # 在这种情况下，将创建一个 Computation 对象，并将其 error 属性设置为 ContractCreationCollision 错误，指示合约创建发生碰撞。
            if is_collision:
                # The address of the newly created contract has *somehow* collided
                # with an existing contract address.
                computation = self.vm_state.get_computation(
                    message, transaction_context
                )
                computation.error = ContractCreationCollision(
                    f"Address collision while creating contract: "
                    f"{encode_hex(message.storage_address)}"
                )
                self.vm_state.logger.debug2(
                    "Address collision while creating contract: %s",
                    encode_hex(message.storage_address),
                )
            else:
                # 没有地址碰撞，表示合约可以成功创建，于是执行合约创建操作。
                computation = self.vm_state.computation_class.apply_create_message(
                    self.vm_state,
                    message,
                    transaction_context,
                )
        else:
            # 如果消息类型不是创建（create），那么执行普通消息调用操作
            computation = self.vm_state.computation_class.apply_message(
                self.vm_state,
                message,
                transaction_context,
            )
        # 返回创建或执行消息调用操作的 Computation 对象
        return computation

    @classmethod
    def calculate_gas_refund(cls, computation: ComputationAPI, gas_used: int) -> int:
        # Self Destruct Refunds
        num_deletions = len(computation.get_accounts_for_deletion())
        if num_deletions:
            computation.refund_gas(REFUND_SELFDESTRUCT * num_deletions)

        # Gas Refunds
        gas_refunded = computation.get_gas_refund()

        return min(gas_refunded, gas_used // MAX_REFUND_QUOTIENT)

    # 负责确保交易的费用正确计算、退还，并处理自毁合约。自毁合约的余额将被转移，
    # 而未使用的 gas 将被返还给交易发起者。
    # 最后，交易费用将支付给矿工作为激励，而自毁合约的账户将被删除。
    def finalize_computation(
        self, transaction: SignedTransactionAPI, computation: ComputationAPI
    ) -> ComputationAPI:
        # 获取当前交易的上下文信息，这包括了 gas 价格等信息，它会在后面用于计算费用。
        transaction_context = self.vm_state.get_transaction_context(transaction)

        # 从 computation 对象中获取剩余的 gas 数量，computation 对象包含了交易执行的结果和状态。
        gas_remaining = computation.get_gas_remaining()
        # 计算已使用的 gas 数量，即交易允许的 gas 数量减去剩余的 gas 数量。
        gas_used = transaction.gas - gas_remaining
        # 通过调用 calculate_gas_refund 方法，计算 gas 的退款。
        # 这通常发生在自毁合约时，未使用的 gas 被返还给交易发起者。
        gas_refund = self.calculate_gas_refund(computation, gas_used)
        # 计算退款的金额，即退还的 gas 数量（gas_refund）加上剩余的 gas 数量（gas_remaining），
        # 然后乘以当前交易的 gas 价格
        gas_refund_amount = (gas_refund + gas_remaining) * transaction_context.gas_price

        # 检查是否有退款金额。如果退款金额大于 0，表示有退款要返还
        if gas_refund_amount:
            self.vm_state.logger.debug2(
                "TRANSACTION REFUND: %s -> %s",
                gas_refund_amount,
                encode_hex(computation.msg.sender),
            )
            # 将退款金额添加到接收者的余额中，表示退还剩余的 gas
            self.vm_state.delta_balance(computation.msg.sender, gas_refund_amount)

        # 重新计算已使用的 gas 数量，除了剩余的 gas 和退款金额之外
        gas_used = transaction.gas - gas_remaining - gas_refund
        # 计算交易费用，即已使用的 gas 数量乘以交易的 gas 价格，这将支付给矿工作为交易费
        transaction_fee = gas_used * self.vm_state.get_tip(transaction)

        # EIP-161:
        # Even if the txn fee is zero, the coinbase is still touched here. Post-merge,
        # with no block reward, in the cases where the txn fee is also zero, the
        # coinbase may end up zeroed after the computation and thus should be marked
        # for deletion since it was touched.
        # 记录交易费用的日志信息，包括费用金额和矿工（coinbase）地址
        self.vm_state.logger.debug2(
            "TRANSACTION FEE: %s -> %s",
            transaction_fee,
            encode_hex(self.vm_state.coinbase),
        )
        # 将交易费用添加到矿工的余额中
        self.vm_state.delta_balance(self.vm_state.coinbase, transaction_fee)

        # 迭代 computation 中获取将要删除的账户（自毁合约的账户）
        for account, _ in computation.get_accounts_for_deletion():
            # 记录将要删除的账户的日志信息
            self.vm_state.logger.debug2("DELETING ACCOUNT: %s", encode_hex(account))
            # 删除自毁合约的账户
            self.vm_state.delete_account(account)

        return computation


class FrontierState(BaseState):
    computation_class: Type[ComputationAPI] = FrontierComputation
    transaction_context_class: Type[TransactionContextAPI] = FrontierTransactionContext
    account_db_class: Type[AccountDatabaseAPI] = AccountDB
    transaction_executor_class: Type[
        TransactionExecutorAPI
    ] = FrontierTransactionExecutor

    # todo 交易处理函数
    def apply_transaction(self, transaction: SignedTransactionAPI) -> ComputationAPI:
        executor = self.get_transaction_executor()
        return executor(transaction)

    def validate_transaction(self, transaction: SignedTransactionAPI) -> None:
        validate_frontier_transaction(self, transaction)
