from typing import (
    Type,
)

from eth_utils import (
    encode_hex,
)

from eth.abc import (
    ComputationAPI,
    SignedTransactionAPI,
    TransactionExecutorAPI,
)
from eth.vm.forks.homestead.state import (
    HomesteadState,
    HomesteadTransactionExecutor,
)

from ._utils import (
    collect_touched_accounts,
)
from .computation import (
    SpuriousDragonComputation,
)


class SpuriousDragonTransactionExecutor(HomesteadTransactionExecutor):
    def finalize_computation(
        self, transaction: SignedTransactionAPI, computation: ComputationAPI
    ) -> ComputationAPI:
        # 调用父类（TransactionExecutorAPI）的 finalize_computation 方法，这一步会根据交易和计算结果生成最终的 Computation 对象
        computation = super().finalize_computation(transaction, computation)

        #
        # EIP161 state clearing
        # 从计算结果中获取所有被访问过的账户地址，然后将这些账户地址存储在 touched_accounts 列表中
        touched_accounts = collect_touched_accounts(computation)

        # 对于 touched_accounts 列表中的每个账户地址，执行以下操作：
        for account in touched_accounts:
            should_delete = self.vm_state.account_exists(
                account
            ) and self.vm_state.account_is_empty(account)
            # 如果账户存在且为空，should_delete 为真，表示这个账户需要被删除。
            if should_delete:
                self.vm_state.logger.debug2(
                    "CLEARING EMPTY ACCOUNT: %s",
                    encode_hex(account),
                )
                self.vm_state.delete_account(account)

        return computation


class SpuriousDragonState(HomesteadState):
    computation_class: Type[ComputationAPI] = SpuriousDragonComputation
    transaction_executor_class: Type[
        TransactionExecutorAPI
    ] = SpuriousDragonTransactionExecutor
