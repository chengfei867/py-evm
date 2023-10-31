from typing import (
    Type,
)

from eth.abc import (
    ComputationAPI,
    MessageAPI,
    SignedTransactionAPI,
    TransactionExecutorAPI,
)
from eth.vm.forks.muir_glacier.state import (
    MuirGlacierState,
)
from eth.vm.forks.spurious_dragon.state import (
    SpuriousDragonTransactionExecutor,
)

from .computation import (
    BerlinComputation,
)


class BerlinTransactionExecutor(SpuriousDragonTransactionExecutor):
    # 创建 EVM 执行的计算对象（Computation），并在执行前设置了一些上下文信息
    # 这段代码用于准备 EVM 执行的计算对象，并确保在执行期间正确标记了地址和存储槽的访问状态，
    # 以符合 EIP-2929 的规范。这些操作有助于在交易执行期间跟踪和控制哪些地址和存储槽被访问，以便正确计算 gas 成本。这是以太坊柏林硬分叉中引入的一项改进。
    # EIP-2929改变了所有这些值，但在此之前，我们需要先谈谈这个EIP引入的一个重要概念：已访问地址和已访问存储密钥。
    # 如果地址或存储密钥以前在交易期间被“使用”，则该地址或存储密钥就被视为已访问。例如，当你调用另一个合约时，该合约的地址会被标记为已访问。
    # 类似地，当你SLOAD或SSTORE某些slot时，它将被视为在交易的其余部分已被访问。不管是哪个操作码做的：如果一个SLOAD读取了一个slot，那么它将被认为对接下来的SLOAD以及SSTORE都是已访问的。
    # 在柏林硬分叉之前，SLOAD的固定成本是800 gas，现在，这取决于是否已访问了存储slot。
    # 如果未访问，则成本为2100 gas，如果已访问，则成本为100 gas。
    # 因此，如果slot在已访问的存储密钥列表中，则一次SLOAD的成本会降低2000 gas。
    def build_computation(
        self, message: MessageAPI, transaction: SignedTransactionAPI
    ) -> ComputationAPI:
        # 此行代码用于标记发送者的地址（transaction.sender）在执行期间被访问过。这是一个与 EIP-2929 相关的操作，用于确定账户是否在交易期间被访问过。
        self.vm_state.mark_address_warm(transaction.sender)

        # 标记消息的 storage_address（消息的存储地址）
        self.vm_state.mark_address_warm(message.storage_address)

        # 循环处理 transaction.access_list,transaction.access_list 是一个列表，其中包含一组访问列表条目。每个条目都包括一个地址（address）和一组存储槽（slots）
        for address, slots in transaction.access_list:
            # 标记地址（address）在执行期间被访问过。
            self.vm_state.mark_address_warm(address)
            # 针对每个存储槽（slot）：self.vm_state.mark_storage_warm(address, slot)，标记存储槽在执行期间被访问过。
            for slot in slots:
                self.vm_state.mark_storage_warm(address, slot)
        #  调用父类的 build_computation 方法，创建并返回 EVM 执行的计算对象（Computation）
        return super().build_computation(message, transaction)


class BerlinState(MuirGlacierState):
    computation_class = BerlinComputation
    transaction_executor_class: Type[TransactionExecutorAPI] = BerlinTransactionExecutor
