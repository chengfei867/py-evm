import contextlib
import itertools
import logging
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
)

from cached_property import (
    cached_property,
)
from eth_hash.auto import (
    keccak,
)
from eth_typing import (
    Address,
    Hash32,
)
from eth_utils import (
    ValidationError,
    encode_hex,
)
import rlp

from eth._utils.datatypes import (
    Configurable,
)
from eth._utils.db import (
    get_block_header_by_hash,
    get_parent_header,
)
from eth.abc import (
    AtomicDatabaseAPI,
    BlockAndMetaWitness,
    BlockAPI,
    BlockHeaderAPI,
    ChainContextAPI,
    ChainDatabaseAPI,
    ComputationAPI,
    ConsensusAPI,
    ConsensusContextAPI,
    ExecutionContextAPI,
    ReceiptAPI,
    ReceiptBuilderAPI,
    SignedTransactionAPI,
    StateAPI,
    TransactionBuilderAPI,
    UnsignedTransactionAPI,
    VirtualMachineAPI,
    WithdrawalAPI,
)
from eth.consensus.pow import (
    PowConsensus,
)
from eth.constants import (
    GENESIS_PARENT_HASH,
    MAX_PREV_HEADER_DEPTH,
    MAX_UNCLES,
)
from eth.db.trie import (
    make_trie_root_and_nodes,
)
from eth.exceptions import (
    HeaderNotFound,
)
from eth.rlp.headers import (
    BlockHeader,
)
from eth.rlp.sedes import (
    uint32,
)
from eth.validation import (
    validate_gas_limit,
    validate_length_lte,
)
from eth.vm.execution_context import (
    ExecutionContext,
)
from eth.vm.interrupt import (
    EVMMissingData,
)
from eth.vm.message import (
    Message,
)

if TYPE_CHECKING:
    from eth.typing import (  # noqa: F401
        Block,
    )


class VM(Configurable, VirtualMachineAPI):
    block_class: Type[BlockAPI] = None  # 定义了区块类型，默认为空
    consensus_class: Type[ConsensusAPI] = PowConsensus  # 定义了共识类型，默认为pow
    extra_data_max_bytes: ClassVar[int] = 32  # 表示区块头中 extra_data 字段的最大字节数。默认设置为 32 字节
    fork: str = None  # 用于指定虚拟机运行的分叉（fork）版本。默认情况下，它被设置为 None，表示没有指定分叉版本。
    chaindb: ChainDatabaseAPI = None  # 表示虚拟机使用的链数据库（Chain Database）。默认情况下，它被设置为 None，表示没有指定链数据库。
    _state_class: Type[StateAPI] = None  # 用于指定虚拟机中使用的状态类。默认情况下，它被设置为 None，表示没有指定状态类。

    # 它们用于存储虚拟机的当前状态和当前区块。默认情况下，它们都被设置为 None，表示虚拟机尚未初始化状态和区块。
    _state = None
    _block = None

    # 用于记录虚拟机的日志信息。它使用 Python 的 logging 模块创建一个名为 "eth.vm.base.VM" 的日志记录器，以便在虚拟机中记录日志信息。
    cls_logger = logging.getLogger("eth.vm.base.VM")

    # 初始化虚拟机的实例
    def __init__(
            self,
            header: BlockHeaderAPI,  # 表示虚拟机的初始化区块头，是一个实现了 BlockHeaderAPI 接口的对象。
            chaindb: ChainDatabaseAPI,  # 表示虚拟机使用的链数据库，是一个实现了 ChainDatabaseAPI 接口的对象。
            chain_context: ChainContextAPI,  # 表示虚拟机的链上下文，是一个实现了 ChainContextAPI 接口的对象。
            consensus_context: ConsensusContextAPI,  # 表示虚拟机的共识上下文，是一个实现了 ConsensusContextAPI 接口的对象。
    ) -> None:  # 构造函数返回 None，表示没有明确的返回值
        self.chaindb = chaindb  # 将传入的 chaindb 参数赋值给虚拟机实例的 chaindb 属性，以便在后续的方法中可以访问和操作链数据库。
        self.chain_context = chain_context  # 将传入的 chain_context 参数赋值给虚拟机实例的 chain_context 属性，以便在虚拟机中可以使用链上下文的相关信息。
        self.consensus_context = consensus_context  # 将传入的 consensus_context 参数赋值给虚拟机实例的 consensus_context 属性，以便在虚拟机中可以使用共识上下文的相关信息。
        self._initial_header = header  # 将传入的 header 参数赋值给虚拟机实例的 _initial_header 属性。这个属性存储了虚拟机的初始化区块头，后续虚拟机运行时可能会用到这个区块头的信息。

    # 获取虚拟机当前的区块头,返回类型注解为 BlockHeaderAPI，表示返回的对象必须实现 BlockHeaderAPI 接口。
    def get_header(self) -> BlockHeaderAPI:
        if self._block is None:  # 检查虚拟机实例的 _block 属性是否为 None。_block 属性存储了虚拟机当前处理的区块，如果为 None，则说明虚拟机尚未处理任何区块。
            return self._initial_header  # 如果 _block 为 None，则返回虚拟机实例的 _initial_header 属性。这个属性是虚拟机在初始化时传入的区块头，用于表示虚拟机的起始状态。
        else:
            return self._block.header  # 如果 _block 不为 None，则返回虚拟机当前处理的区块（_block）的区块头属性（header）。这表示虚拟机正在处理一个特定的区块，而不是初始状态的区块。

    # 用于获取虚拟机当前处理的区块。它的返回类型注解为 BlockAPI，表示返回的对象必须是一个区块对象，实现了 BlockAPI 接口。
    def get_block(self) -> BlockAPI:
        if self._block is None:  # 检查虚拟机实例的 _block 属性是否为 None。_block 属性存储了虚拟机当前处理的区块，如果为 None，则说明虚拟机尚未处理任何区块。
            block_class = self.get_block_class()  # 如果 _block 为 None，则获取虚拟机的区块类，通过调用 self.get_block_class() 方法获得。这个类用于创建区块对象。
            # 使用 block_class 创建一个新的区块对象，通过调用 from_header 类方法。from_header 方法接受两个参数：
            # header：虚拟机初始化时传入的初始区块头，作为新区块的头部。
            # chaindb：虚拟机的链数据库，用于在区块对象中操作数据库。
            self._block = block_class.from_header(
                header=self._initial_header, chaindb=self.chaindb
            )
        # 最后，无论是创建了新的区块对象还是使用已存在的区块对象，都返回虚拟机实例的 _block 属性，表示当前处理的区块。
        return self._block

    # 获取虚拟机的状态。它的返回类型注解为 StateAPI，表示返回的对象必须是一个实现了 StateAPI 接口的状态对象。
    @property
    def state(self) -> StateAPI:
        if self._state is None:
            # 如果 _state 为 None，则执行build_state方法，创建一个新的状态对象。
            self._state = self.build_state(
                self.chaindb.db,  # 这是虚拟机实例的链数据库中的数据库。状态对象需要访问链数据库来获取状态数据。
                self.get_header(),  # 通过调用 self.get_header() 方法获取当前处理的区块的区块头。
                self.chain_context,  # 虚拟机实例的链上下文，用于获取链的相关信息。
                self.previous_hashes,  # 通过调用 self.previous_hashes 属性获取先前区块的哈希列表。这些哈希值用于初始化状态对象。
            )
        return self._state

    # 这个方法的主要作用是根据传入的数据库、区块头和执行上下文来构建一个虚拟机的状态对象。
    # 状态对象用于跟踪虚拟机的当前状态，包括账户、存储、合同代码等信息。
    # 在虚拟机的执行过程中，状态对象会被不断更新和修改，以反映虚拟机的状态变化。
    @classmethod
    def build_state(
            cls,  # 这个参数表示类自身，即 VM 类。在类方法中，通常使用 cls 来引用类的属性和方法。
            db: AtomicDatabaseAPI,  # 表示一个原子数据库，通常用于存储区块链的状态数据。db 参数用于访问数据库。
            header: BlockHeaderAPI,  # 表示区块头对象，包含了关于区块的信息，如区块号、时间戳、难度等。
            chain_context: ChainContextAPI,  # 表示链上下文对象，用于获取链的相关信息。
            previous_hashes: Iterable[Hash32] = (),  # 这是一个可选参数，表示前一个区块的哈希列表，默认为空。previous_hashes 参数用于构建状态对象时考虑先前的区块状态。
    ) -> StateAPI:  # build_state 方法将返回一个实现了 StateAPI 接口的状态对象。
        # 在方法中首先调用了 cls.create_execution_context 方法，该方法用于创建执行上下文（ExecutionContext）对象。
        execution_context = cls.create_execution_context(
            header, previous_hashes, chain_context
        )
        # 然后，使用类属性 get_state_class 获取状态对象的类，并实例化一个状态对象。这个状态对象的构造需要传入数据库 (db)、执行上下文 (execution_context) 和状态根 (header.state_root)。
        return cls.get_state_class()(db, execution_context, header.state_root)

    # cached_property将方法转化为一个缓存的属性。
    # 这意味着第一次访问属性时，会调用该方法来计算属性的值，然后将其缓存起来，以后再次访问属性时，将直接返回缓存的值，而不会重新计算。
    @cached_property
    # 获取虚拟机的共识对象。
    def _consensus(self) -> ConsensusAPI:
        # 通过调用虚拟机的 consensus_class 属性，传入虚拟机的 consensus_context 参数来创建一个共识对象，并将其返回。
        return self.consensus_class(self.consensus_context)

    #
    # Logging
    #
    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"eth.vm.base.VM.{self.__class__.__name__}")

    #
    # Execution
    # 这段代码表示了处理以太坊区块中交易的流程，包括验证交易、执行交易、生成收据和验证收据，以确保交易的合法性和一致性。
    def apply_transaction(
            # header 表示区块头，
            # transaction 表示已签名的交易的参数。
            self, header: BlockHeaderAPI, transaction: SignedTransactionAPI
            # 方法将返回一个包含两个元素的元组，分别是 ReceiptAPI 和 ComputationAPI。
            # ReceiptAPI 和 ComputationAPI 是以太坊中的两个重要接口，它们用于表示交易执行的结果和信息，以及与交易相关的数据。
    ) -> Tuple[ReceiptAPI, ComputationAPI]:
        # 这个方法用于验证交易是否符合区块头的要求
        self.validate_transaction_against_header(header, transaction)

        # 将当前的状态标记为不可回滚状态，因为一个新的交易即将开始执行。这是为了确保在执行新交易时不会回滚之前的状态更改，以保持状态的一致性。
        self.state.lock_changes()

        # 用于执行交易，即更新以太坊的全局状态。执行后，computation将包含有关交易执行的信息。# todo
        computation = self.state.apply_transaction(transaction)

        # 生成与交易相关的收据（Receipt）。收据包含了有关交易执行结果的信息，如交易状态、花费的燃气量、日志等。 # todo
        receipt = self.make_receipt(header, transaction, computation, self.state)

        # 验证生成的收据的有效性。这是为了确保收据中的数据是合法的，与交易执行结果一致的步骤。
        self.validate_receipt(receipt)

        return receipt, computation

    # 这个方法的主要作用是创建一个执行上下文对象，其中包含了区块头的相关信息，以及用于执行合约代码的上下文数据。
    # 如果区块头包含 base_fee_per_gas 属性，那么也会将其包含在执行上下文中。
    # 执行上下文是在虚拟机执行合同代码时使用的环境，其中包含了执行合约所需的各种参数和信息。
    @classmethod
    def create_execution_context(
            cls,
            header: BlockHeaderAPI,  # 表示区块头对象，包含了关于区块的信息，如区块号、时间戳、难度等。
            prev_hashes: Iterable[Hash32],  # 表示前一区块的哈希列表，是一个可迭代对象。
            chain_context: ChainContextAPI,  # 表示链上下文对象，用于获取链的相关信息。
    ) -> ExecutionContextAPI:  # 返回一个实现了 ExecutionContextAPI 接口的执行上下文对象。
        # 通过类属性 consensus_class 获取共识类，然后调用 get_fee_recipient 方法来获取手续费接收地址（fee_recipient）。这个地址通常是区块的 coinbase 地址。
        fee_recipient = cls.consensus_class.get_fee_recipient(header)
        try:
            base_fee = header.base_fee_per_gas  # 尝试获取区块头的 base_fee_per_gas 属性。
        except AttributeError:
            # 如果未成功获取了 base_fee，则创建一个 ExecutionContext 对象，并传入以下参数：
            return ExecutionContext(
                coinbase=fee_recipient,  # coinbase 地址，即手续费接收地址。
                timestamp=header.timestamp,  # 区块的时间戳。
                block_number=header.block_number,  # 区块号。
                difficulty=header.difficulty,  # 区块的难度。
                mix_hash=header.mix_hash,  # 混合哈希。
                gas_limit=header.gas_limit,  # 区块的气体限制。
                prev_hashes=prev_hashes,  # 前一个区块的哈希列表。
                chain_id=chain_context.chain_id,  # 链的标识。
            )
        else:
            # 若成功获取则也将base_fee加入执行上下文（ExecutionContext）中
            return ExecutionContext(
                coinbase=fee_recipient,
                timestamp=header.timestamp,
                block_number=header.block_number,
                difficulty=header.difficulty,
                mix_hash=header.mix_hash,
                gas_limit=header.gas_limit,
                prev_hashes=prev_hashes,
                chain_id=chain_context.chain_id,
                base_fee_per_gas=base_fee,  # 基础气体费用
            )

    # 负责执行合约字节码（bytecode）。
    def execute_bytecode(
            # 这些参数描述了要执行的合约字节码的相关信息，例如发送者、接收者、燃气限制、输入数据等。
            self,
            origin: Address,
            gas_price: int,
            gas: int,
            to: Address,
            sender: Address,
            value: int,
            data: bytes,
            code: bytes,
            code_address: Address = None,
    ) -> ComputationAPI:
        # 检查 origin，如果 origin 为 None，则将其设置为 sender。origin 表示合约执行的原始调用者（事务的发起者），如果未提供 origin，则默认为 sender。
        if origin is None:
            origin = sender

        # Construct a message
        # 创建一个名为 message 的消息对象，这个对象包含了执行合约所需的所有信息，包括燃气限制、接收地址、发送地址、转账金额、输入数据、合约字节码和合约地址。这个消息对象将用于执行合约。
        message = Message(
            gas=gas,
            to=to,
            sender=sender,
            value=value,
            data=data,
            code=code,
            code_address=code_address,
        )

        # 创建一个名为 transaction_context 的交易上下文对象，用于存储与交易相关的信息
        # 包括燃气价格（gas_price）和原始调用者（origin）。
        # 这个上下文将传递给合约执行过程，以帮助确定交易的燃气费用等信息。
        transaction_context = self.state.get_transaction_context_class()(
            gas_price=gas_price,
            origin=origin,
        )

        # 最后，调用 self.state.computation_class.apply_computation 方法，执行合约字节码。
        # 执行结果将作为 ComputationAPI 对象返回，其中包含了合约执行的详细信息，包括执行状态、燃气消耗、错误信息、日志等。
        # todo
        return self.state.computation_class.apply_computation(
            self.state,
            message,
            transaction_context,
        )

    # apply_all_transactions 的方法用于在虚拟机上批量处理一系列交易（transactions）。
    def apply_all_transactions(
            # transactions 是一系列已签名的交易
            # base_header 是要应用这些交易的基础块头
            self, transactions: Sequence[SignedTransactionAPI], base_header: BlockHeaderAPI
    ) -> Tuple[BlockHeaderAPI, Tuple[ReceiptAPI, ...], Tuple[ComputationAPI, ...]]:
        vm_header = self.get_header()  # 获取虚拟机的当前块头,将用于验证要处理的事务是否与当前虚拟机状态一致。
        # 检查 base_header 的块编号是否与虚拟机的当前块编号一致，如果不一致，抛出 ValidationError 异常，表示虚拟机不能在这个块上执行交易。
        if base_header.block_number != vm_header.block_number:
            raise ValidationError(
                f"This VM instance must only work on block #{self.get_header().block_number}, "  # noqa: E501
                f"but the target header has block #{base_header.block_number}"
            )
        # 初始化空的 receipts 列表，用于存储每个事务的执行结果（收据），
        # 以及空的 computations 列表，用于存储每个事务的计算结果。
        # 同时初始化 previous_header 和 result_header，它们表示正在处理的事务所在的块头。
        receipts = []
        computations = []
        previous_header = base_header
        result_header = base_header

        # 开始遍历给定的交易列表，使用 enumerate 获取交易的索引和事务对象。
        for transaction_index, transaction in enumerate(transactions):
            snapshot = self.state.snapshot()  # 创建一个快照，以便后续可以在事务失败时回滚虚拟机状态
            try:
                # 调用 self.apply_transaction 方法来执行当前交易。 todo
                receipt, computation = self.apply_transaction(
                    previous_header,
                    transaction,
                )
            # 如果交易执行时出现了 EVMMissingData 异常，表示数据不完整，将回滚虚拟机状态，并向上抛出异常
            except EVMMissingData:
                self.state.revert(snapshot)
                raise

            # 将交易的执行结果（收据）添加到 previous_header
            result_header = self.add_receipt_to_header(previous_header, receipt)
            # 然后将 previous_header 更新为新的 result_header，这表示正在处理下一个交易时，块头已经更新为包括前一个交易的执行结果。
            previous_header = result_header
            # 将当前交易的收据和计算结果分别添加到 receipts 和 computations 列表中。
            receipts.append(receipt)
            computations.append(computation)

            # 处理事务应用后的钩子操作，通常用于记录日志或其他后续处理。
            self.transaction_applied_hook(
                transaction_index,
                transactions,
                vm_header,
                result_header,
                computation,
                receipt,
            )
        # 将 receipts 和 computations 列表转换为元组，以便返回结果。
        receipts_tuple = tuple(receipts)
        computations_tuple = tuple(computations)

        return result_header, receipts_tuple, computations_tuple

    # 提款操作
    def apply_withdrawal(
            self,
            withdrawal: WithdrawalAPI,
    ) -> None:
        # 调用state属性中的apply_withdrawal方法，并传递了withdrawal作为参数，将提款操作应用到当前的状态中。
        self.state.apply_withdrawal(withdrawal)

    # 处理一批提款操作
    def apply_all_withdrawals(self, withdrawals: Sequence[WithdrawalAPI]) -> None:
        touched_addresses: List[Address] = []  # 创建了一个空列表变量touched_addresses，用于存储所有受影响的地址。这些地址是在处理提款操作时发生变化的。

        # 迭代处理withdrawals中的每个提款操作。
        for withdrawal in withdrawals:
            # 首先对提款操作进行验证，确保提款操作的字段是有效的。
            withdrawal.validate()

            # 将提款操作应用到当前状态中
            self.apply_withdrawal(withdrawal)

            # 如果提款操作中的地址（withdrawal.address）尚未在touched_addresses列表中，那么它将被添加到列表中。这是为了跟踪哪些地址受到了提款操作的影响。
            if withdrawal.address not in touched_addresses:
                touched_addresses.append(withdrawal.address)

        # 处理所有受影响的地址。
        for address in touched_addresses:
            # 对于每个地址，它会检查如果在应用了所有提款操作后账户为空，就删除该账户。这是一个用于清理状态的步骤，以确保不保留空账户。
            if self.state.account_is_empty(address):
                self.state.delete_account(address)

    # import_block的方法，用于导入新的区块并更新虚拟机的状态。
    # （区块是evm执行的基本单元，这个区块是矿工节点想要挖掘的区块，矿工节点需要将该区块导入evm来执行其中的交易，然后更新本地状态后进行挖矿操作）
    # block（表示要导入的区块）。
    # 返回类型为BlockAndMetaWitness，表明它将返回一个包含区块和元数据见证的对象。
    def import_block(self, block: BlockAPI) -> BlockAndMetaWitness:
        # 检查要导入的区块的编号是否与虚拟机的当前区块编号相匹配。
        if self.get_block().number != block.number:
            # 如果不匹配，它会引发一个ValidationError异常，表示虚拟机只能导入与当前区块编号匹配的区块。
            raise ValidationError(
                f"This VM can only import blocks at number #{self.get_block().number},"
                f" the attempted block was #{block.number}"
            )
        # 创建了一个名为header_params的字典，其中包含了从导入的区块中提取的一些区块头参数。
        # 这些参数包括矿工地址（coinbase）、难度（difficulty）、燃气限制（gas_limit）、时间戳（timestamp）等。
        header_params = {
            "coinbase": block.header.coinbase,
            "difficulty": block.header.difficulty,
            "gas_limit": block.header.gas_limit,
            "timestamp": block.header.timestamp,
            "extra_data": block.header.extra_data,
            "mix_hash": block.header.mix_hash,
            "nonce": block.header.nonce,
            "uncles_hash": keccak(rlp.encode(block.uncles)),
        }
        # 创建了一个名为block_params的字典，其中包括区块头（header）和叔块列表（uncles）。
        # 这里的header是通过调用self.configure_header方法，并传递header_params中的参数创建的。
        block_params = {
            "header": self.configure_header(**header_params),
            "uncles": block.uncles,
        }

        # 检查导入的区块是否包含提款（withdrawals）。
        if hasattr(block, "withdrawals"):
            # 如果是后Shanghai规则的区块（Post-Shanghai Blocks），则将提款数据添加到block_params中，因为withdrawals操作只有在更新到Shanghai规则后才生效
            block_params["withdrawals"] = block.withdrawals

        # 将虚拟机的当前区块（self._block）更新为新创建的区块，新区块的参数来自block_params。
        self._block = self.get_block().copy(**block_params)

        # 创建了一个名为execution_context的对象，用于执行新区块的交易。该上下文包括了区块头（block.header）、之前的区块哈希值（self.previous_hashes）和链上下文（self.chain_context）。
        execution_context = self.create_execution_context(
            block.header, self.previous_hashes, self.chain_context
        )

        # 创建了一个新的区块头（header），并将gas_used设置为零。这是为了在应用交易前将gas_used重置为零。
        header = self.get_header().copy(gas_used=0)

        # 重新初始化了虚拟机的状态（self._state），以确保执行上下文和区块头都已更新。
        self._state = self.get_state_class()(
            self.chaindb.db, execution_context, header.state_root
        )

        # 调用self.apply_all_transactions方法，并传递导入区块的交易列表（block.transactions）和之前创建的区块头（header）。
        # 它会执行所有交易并返回新的区块头、交易收据和计算结果（在这里没有使用，因此使用占位符_）。
        new_header, receipts, _ = self.apply_all_transactions(
            block.transactions, header
        )

        # 如果导入的区块包含提款，这段代码将提取提款数据。如果不包含提款，则withdrawals被设置为None。
        withdrawals = block.withdrawals if hasattr(block, "withdrawals") else None

        # 检查是否存在提款数据（withdrawals），如果有，就调用self.apply_all_withdrawals方法来处理这些提款。
        if withdrawals:
            # post-shanghai blocks
            self.apply_all_withdrawals(block.withdrawals)

        # 调用self.set_block_transactions_and_withdrawals方法，以更新虚拟机的区块，设置新的区块头、交易列表、收据以及提款数据（如果有的话）。
        filled_block = self.set_block_transactions_and_withdrawals(
            self.get_block(),
            new_header,
            block.transactions,
            receipts,
            withdrawals=withdrawals,
        )

        # 该方法返回通过self.mine_block方法挖掘（mine）的新区块，该新区块包含了新的区块头、交易列表、收据和提款数据。
        return self.mine_block(filled_block)

    # 将组装好的区块进行挖矿
    def mine_block(
            self, block: BlockAPI, *args: Any, **kwargs: Any
    ) -> BlockAndMetaWitness:
        # 调用 pack_block 方法来对区块进行打包。在这个过程中，可能需要传入额外的参数 args 和关键字参数 kwargs。
        packed_block = self.pack_block(block, *args, **kwargs)
        # 调用 finalize_block 方法，将打包后的区块作为参数传递。这一步会对区块进行最终化，确保区块满足一系列条件，包括工作量证明（Proof of Work）等。
        # block_result 是一个包含了挖矿结果的对象。
        block_result = self.finalize_block(packed_block)
        # 执行区块的验证操作。这个验证过程确保区块满足所有必要的条件，以确保它是有效的。
        self.validate_block(block_result.block)

        # 返回 block_result，这个对象包含了挖矿后的区块以及与之相关的元数据证明。这个区块可以被广播到整个区块链网络，其他节点会验证并接受它，从而完成了新区块的添加。
        return block_result

    # 主要功能是将区块的交易和提款数据与相应的区块头相关联，最终生成一个新的区块对象。
    def set_block_transactions_and_withdrawals(
            self,
            base_block: BlockAPI,
            new_header: BlockHeaderAPI,
            transactions: Sequence[SignedTransactionAPI],
            receipts: Sequence[ReceiptAPI],
            withdrawals: Sequence[WithdrawalAPI] = None,
    ) -> BlockAPI:
        # 调用 make_trie_root_and_nodes 方法，该方法用于将交易数据构建成 Merkle Trie 结构，并返回交易根哈希 (tx_root_hash) 和相应的 Trie 节点 (tx_kv_nodes)。
        tx_root_hash, tx_kv_nodes = make_trie_root_and_nodes(transactions)
        # 将交易相关的 Trie 节点数据存储到区块链数据库中，以便后续的检索和验证
        self.chaindb.persist_trie_data_dict(tx_kv_nodes)
        # 将交易的收据（receipts）数据也构建成 Merkle Trie 结构，并返回收据根哈希 (receipt_root_hash) 和相应的 Trie 节点 (receipt_kv_nodes)。
        receipt_root_hash, receipt_kv_nodes = make_trie_root_and_nodes(receipts)
        # 将收据相关的 Trie 节点数据存储到区块链数据库中。
        self.chaindb.persist_trie_data_dict(receipt_kv_nodes)
        # 定义了一个 block_fields 字典，将其中的 "transactions" 键与传入的 transactions 关联起来。
        block_fields: "Block" = {"transactions": transactions}
        # 定义了一个 block_header_fields 字典，将其中的 "transaction_root" 和 "receipt_root" 与交易根哈希和收据根哈希关联起来。
        block_header_fields = {
            "transaction_root": tx_root_hash,
            "receipt_root": receipt_root_hash,
        }

        # 如果存在提款数据，执行以下操作。
        if withdrawals:
            # 如果存在提款数据，将提款数据构建成 Merkle Trie 结构，并返回提款根哈希 (withdrawals_root_hash) 和相应的 Trie 节点 (withdrawal_kv_nodes)。
            withdrawals_root_hash, withdrawal_kv_nodes = make_trie_root_and_nodes(
                withdrawals,
            )
            # 将提款相关的 Trie 节点数据存储到区块链数据库中。
            self.chaindb.persist_trie_data_dict(withdrawal_kv_nodes)
            # 将提款数据与 block_fields 字典中的 "withdrawals" 键相关联。
            block_fields["withdrawals"] = withdrawals
            # 提款根哈希与 block_header_fields 字典中的 "withdrawals_root" 键相关联。
            block_header_fields["withdrawals_root"] = withdrawals_root_hash
        # 将新的区块头（new_header）与 block_fields 字典中的 "header" 键相关联，构建一个包含了所有必要数据的新区块对象。
        block_fields["header"] = new_header.copy(**block_header_fields)

        # 新构建的区块对象与传入的 base_block 区块对象相关联，生成一个新的区块，并将其返回
        return base_block.copy(**block_fields)

    # 用于为一个区块分配奖励
    def _assign_block_rewards(self, block: BlockAPI) -> None:
        # 首先，它计算一个总的区块奖励 (block_reward)。这个奖励由以下两部分组成：
        # self.get_block_reward()：获取区块的基本奖励，通常由协议规定。
        # len(block.uncles) * self.get_nephew_reward()：计算叔块（uncles）的数量乘以每个叔块的奖励（nephew reward）之和。叔块是指与当前区块的父区块不同但依然被包含在区块链中的区块。这是一种额外的奖励机制，用于鼓励矿工包括叔块在内。
        block_reward = self.get_block_reward() + (
                len(block.uncles) * self.get_nephew_reward()
        )

        # EIP-161:
        # Even if block reward is zero, the coinbase is still touched here. This was
        # not likely to ever happen in PoW, except maybe in some very niche cases, but
        # happens now in PoS. In these cases, the coinbase may end up zeroed after the
        # computation and thus should be marked for deletion since it was touched.
        # 将计算出的 block_reward 添加到区块的 coinbase 地址上。Coinbase 地址是指接收区块奖励的地址，通常是矿工的地址。这一步将增加 coinbase 地址的余额，表示为区块奖励的分配。
        self.state.delta_balance(block.header.coinbase, block_reward)
        self.logger.debug(
            "BLOCK REWARD: %s -> %s",
            block_reward,
            encode_hex(block.header.coinbase),
        )
        # 进入一个循环，用于处理叔块（uncles）的奖励
        for uncle in block.uncles:
            # 计算每个叔块的奖励，这通常是根据叔块的高度和一些规则来确定的
            uncle_reward = self.get_uncle_reward(block.number, uncle)
            self.logger.debug(
                "UNCLE REWARD REWARD: %s -> %s",
                uncle_reward,
                encode_hex(uncle.coinbase),
            )
            # 将叔块奖励添加到叔块的 coinbase 地址上，以完成叔块奖励的分配
            self.state.delta_balance(uncle.coinbase, uncle_reward)

    # 在准备要添加到区块链的区块上进行最后的处理
    def finalize_block(self, block: BlockAPI) -> BlockAndMetaWitness:
        # 检查当前处理的区块的高度是否大于 0，因为只有区块高度大于 0（创世区块之后的区块）才会分配奖励。如果是创世区块，就没有奖励可分配。
        if block.number > 0:
            # 在执行分配奖励之前，它创建一个状态快照。
            snapshot = self.state.snapshot()
            try:
                # 调用 _assign_block_rewards 方法来计算和分配区块奖励。
                self._assign_block_rewards(block)
            except EVMMissingData:
                # 如果出现错误，状态将被回滚到之前的状态。
                self.state.revert(snapshot)
                raise
            else:
                # 计算和分配奖励成功完成，它会提交状态快照，将状态更改应用到区块链状态中。
                self.state.commit(snapshot)

        # We need to call `persist` here since the state db batches
        # all writes until we tell it to write to the underlying db
        # 调用 self.state.persist() 方法，将在状态处理期间创建的更改写入到底层数据库中。这是为了确保所有状态更改被永久化。
        meta_witness = self.state.persist()

        # 创建了一个新的区块 final_block，其头部中的状态根（state_root）被设置为当前状态的根。
        # 这表示区块已经包含了经过奖励分配后的最终状态。
        final_block = block.copy(
            header=block.header.copy(state_root=self.state.state_root)
        )

        self.logger.debug(
            "%s reads %d unique node hashes, %d addresses, %d bytecodes, and %d storage slots",  # noqa: E501
            final_block,
            len(meta_witness.hashes),
            len(meta_witness.accounts_queried),
            len(meta_witness.account_bytecodes_queried),
            meta_witness.total_slots_queried,
        )

        # 返回一个由 final_block 和 meta_witness 组成的元组，其中 final_block 是已经处理和准备好的最终区块，而 meta_witness 包含有关区块处理期间产生的信息。
        return BlockAndMetaWitness(final_block, meta_witness)

    # 主要功能是创建一个新的区块对象，用于包含即将添加到区块链的数据，该方法允许修改区块头的一些信息
    # pack_block 函数通常在以下情况下被调用，以修改或自定义区块头的信息：
    def pack_block(self, block: BlockAPI, *args: Any, **kwargs: Any) -> BlockAPI:
        # 检查 kwargs 中是否包含 "uncles" 这个字段。如果包含，就表示在调用该方法时传递了 "uncles" 参数。
        if "uncles" in kwargs:
            # 如果 "uncles" 参数存在，它会从 kwargs 中取出 "uncles" 的值，并将其赋给 uncles 变量。同时，从 kwargs 中删除 "uncles" 字段。
            uncles = kwargs.pop("uncles")
            kwargs.setdefault("uncles_hash", keccak(rlp.encode(uncles)))
        else:
            # 如果 "uncles" 参数不存在，就表示使用了原始区块对象 block 中的 uncles 字段。
            uncles = block.uncles

        # 创建一个包含 kwargs 中所有字段名称的集合，用于跟踪哪些字段在 kwargs 中提供了值。
        provided_fields = set(kwargs.keys())
        # 创建一个包含 BlockHeader 类的字段名称的集合，用于表示 BlockHeader 类已知的字段。
        known_fields = set(BlockHeader._meta.field_names)
        # 计算 provided_fields 和 known_fields 之间的差集，找出在 kwargs 中提供了但 BlockHeader 类不认识的字段。
        unknown_fields = provided_fields.difference(known_fields)

        # 如果存在未知字段，它会引发 AttributeError 异常，表示无法设置 BlockHeader 类中的这些未知字段。
        if unknown_fields:
            raise AttributeError(
                f"Unable to set the field(s) {', '.join(known_fields)} "
                "on the `BlockHeader` class. "
                f"Received the following unexpected fields: {', '.join(unknown_fields)}."  # noqa: E501
            )
        # 使用 block.header 创建一个新的 BlockHeader 对象 header，并将 kwargs 中的字段值应用到该新对象上。
        header: BlockHeaderAPI = block.header.copy(**kwargs)
        # 创建一个新的区块对象 packed_block，将 uncles 和新创建的 header 设置为新对象的属性。
        packed_block = block.copy(uncles=uncles, header=header)

        # 返回这个新的区块对象，其中包含了新的 uncles 和经过更新的 header。
        return packed_block

    # 生成一个新的区块对象，给定父区块头信息和 coinbase 地址。
    @classmethod
    def generate_block_from_parent_header_and_coinbase(
            cls, parent_header: BlockHeaderAPI, coinbase: Address
    ) -> BlockAPI:
        # 创建了一个新的区块头对象，使用 cls 的 create_header_from_parent 方法，传入 parent_header 作为父区块头，以及 coinbase 作为coinbase地址。
        block_header = cls.create_header_from_parent(parent_header, coinbase=coinbase)
        # 创建了一个新的区块对象 block，使用 cls 的 get_block_class 方法来获取区块对象的类。
        # 传入 block_header 作为区块头，以及空的交易列表 transactions 和空的叔区块列表 uncles。
        block = cls.get_block_class()(
            block_header,
            transactions=[],
            uncles=[],
        )
        return block

    # 用来创建创世区块（Genesis Block）的区块头（Block Header）
    @classmethod
    def create_genesis_header(cls, **genesis_params: Any) -> BlockHeaderAPI:
        # 通过调用 cls 的 create_header_from_parent 方法来创建创世区块的区块头。
        # 创世区块的父区块为空（None），并且通过传入 genesis_params 参数来设置其他区块头的属性，这些属性通常包括难度、时间戳、初始的状态根等。
        return cls.create_header_from_parent(None, **genesis_params)

    # 用来获取虚拟机（VM）实例的区块（Block）类的
    @classmethod
    def get_block_class(cls) -> Type[BlockAPI]:
        if cls.block_class is None:
            # 检查虚拟机实例是否已经设置了区块类 (block_class)。如果还没有设置，会引发 AttributeError 异常，说明需要先设置区块类。
            raise AttributeError("No `block_class` has been set for this VM")
        else:
            # 如果区块类已经设置，就返回它
            return cls.block_class

    @classmethod
    def get_prev_hashes(
            cls, last_block_hash: Hash32, chaindb: ChainDatabaseAPI
    ) -> Optional[Iterable[Hash32]]:
        # 创世区块无前驱区块
        if last_block_hash == GENESIS_PARENT_HASH:
            return

        # 区块链数据库 (chaindb) 中获取最新区块的区块头（block header）
        block_header = get_block_header_by_hash(last_block_hash, chaindb)

        # 最多遍历256次（MAX_PREV_HEADER_DEPTH = 256）
        for _ in range(MAX_PREV_HEADER_DEPTH):
            # 将当前区块头的哈希值作为生成器的下一个值返回（每次调用get_prev_hashes函数都只会返回前一个区块的哈希，当下一次执行get_prev_hashes函数时会回到yield处执行）
            yield block_header.hash
            try:
                # 尝试获取前一区块的区块头（通过 get_parent_header 函数）。
                block_header = get_parent_header(block_header, chaindb)
            except (IndexError, HeaderNotFound):
                # 如果成功获取，循环继续。如果没有更多的前一区块可用（触发了 IndexError 或 HeaderNotFound 异常），循循环终止。
                break

    # 调用上面的函数获取前一区块哈希
    @property
    def previous_hashes(self) -> Optional[Iterable[Hash32]]:
        return self.get_prev_hashes(self.get_header().parent_hash, self.chaindb)

    # 创建一个新交易
    def create_transaction(self, *args: Any, **kwargs: Any) -> SignedTransactionAPI:
        return self.get_transaction_builder().new_transaction(*args, **kwargs)

    @classmethod
    def create_unsigned_transaction(
            cls,
            *,
            nonce: int,
            gas_price: int,
            gas: int,
            to: Address,
            value: int,
            data: bytes,
    ) -> UnsignedTransactionAPI:
        # 调用 cls.get_transaction_builder().create_unsigned_transaction(...) 来创建一个未签名的交易对象
        return cls.get_transaction_builder().create_unsigned_transaction(
            nonce=nonce, gas_price=gas_price, gas=gas, to=to, value=value, data=data
        )

    # 获得交易构建器
    @classmethod
    def get_transaction_builder(cls) -> Type[TransactionBuilderAPI]:
        return cls.get_block_class().get_transaction_builder()

    # 获得收据构建器
    @classmethod
    def get_receipt_builder(cls) -> Type[ReceiptBuilderAPI]:
        return cls.get_block_class().get_receipt_builder()

    # 验证收据
    @classmethod
    def validate_receipt(cls, receipt: ReceiptAPI) -> None:
        # 创建了一个空集合 already_checked，用于跟踪已经检查过的地址。这是一个辅助数据结构，用于确保检查过的地址不会被多次检查。
        already_checked: Set[Union[Address, int]] = set()

        # 遍历收据中的日志列表
        for log_idx, log in enumerate(receipt.logs):
            # 已经检查过，就直接跳过当前日志，不再重复检查
            if log.address in already_checked:
                continue
            # 如果地址没有在 already_checked 中，接着检查地址是否在收据的布隆过滤器（bloom filter）中。
            elif log.address not in receipt.bloom_filter:
                # 如果地址不在布隆过滤器中，说明收据中包含了一个地址，但该地址在布隆过滤器中未被标记，这可能是一个异常情况。因此，此代码引发一个 ValidationError 异常，指示出现了问题。
                raise ValidationError(
                    f"The address from the log entry at position {log_idx} is not "
                    "present in the provided bloom filter."
                )
            already_checked.add(log.address)

        for log_idx, log in enumerate(receipt.logs):
            for topic_idx, topic in enumerate(log.topics):
                if topic in already_checked:
                    continue
                elif uint32.serialize(topic) not in receipt.bloom_filter:
                    raise ValidationError(
                        f"The topic at position {topic_idx} from the log entry at "
                        f"position {log_idx} is not present in the provided bloom filter."  # noqa: E501
                    )
                # 如果地址通过了检查，将其添加到 already_checked 集合中，以便后续不会重复检查。
                already_checked.add(topic)

    # 方法的主要目的是确保接收到的区块的各个属性（如交易根、状态根、叔块等）是正确的，从而确保区块的有效性。
    def validate_block(self, block: BlockAPI) -> None:
        # 检查传入的 block 是否是正确的区块类型，通过比较其类型是否与 VM 实例中配置的区块类型相匹配
        if not isinstance(block, self.get_block_class()):
            raise ValidationError(
                f"This vm ({self!r}) is not equipped to validate a block of type {block!r}"  # noqa: E501
            )

        # 检查区块是否是创世块（genesis block）。如果是创世块，执行以下验证。
        if block.is_genesis:
            # 对区块头的额外数据（extra_data）进行验证，确保其长度不超过预定义的最大字节数（self.extra_data_max_bytes）。
            # 这个验证是为了确保创世块的额外数据不过大。
            validate_length_lte(
                block.header.extra_data,
                self.extra_data_max_bytes,
                title="BlockHeader.extra_data",
            )
        # 如果区块不是创世块，执行以下验证。
        else:
            # 获取当前区块的父区块头。
            parent_header = get_parent_header(block.header, self.chaindb)
            # 验证当前区块的区块头（block.header）和其父区块头（parent_header）
            self.validate_header(block.header, parent_header)

        # 计算当前区块的交易根哈希值
        tx_root_hash, _ = make_trie_root_and_nodes(block.transactions)
        # 比较计算得到的交易根哈希值和区块头中的交易根哈希值，确保它们一致。
        if tx_root_hash != block.header.transaction_root:
            # 如果不一致，表示区块的交易根哈希值不正确。
            raise ValidationError(
                f"Block's transaction_root ({block.header.transaction_root!r}) "
                f"does not match expected value: {tx_root_hash!r}"
            )

        # 检查区块中包含的叔块（uncles）的数量是否超过了最大允许数量。
        if len(block.uncles) > MAX_UNCLES:
            raise ValidationError(
                f"Blocks may have a maximum of {MAX_UNCLES} uncles.  "
                f"Found {len(block.uncles)}."
            )
        # 检查当前区块的状态根是否存在于数据库中
        if not self.chaindb.exists(block.header.state_root):
            # 检查当前状态根是否与区块头中的状态根相匹配。如果不匹配，表示状态根不正确。
            if not self.state.make_state_root() == block.header.state_root:
                raise ValidationError(
                    "`state_root` does not match or was not found in the db.\n"
                    f"- state_root: {block.header.state_root!r}"
                )

        # 计算当前区块的叔块哈希值
        local_uncle_hash = keccak(rlp.encode(block.uncles))
        # 比较计算得到的叔块哈希值和区块头中的叔块哈希值，确保它们一致。如果不一致，表示叔块哈希不正确
        if local_uncle_hash != block.header.uncles_hash:
            raise ValidationError(
                "`uncles_hash` and block `uncles` do not match.\n"
                f" - num_uncles       : {len(block.uncles)}\n"
                f" - block uncle_hash : {local_uncle_hash!r}\n"
                f" - header uncle_hash: {block.header.uncles_hash!r}"
            )

    # 方法主要用于确保接收到的区块头的各个属性（如额外数据、gas 限制、时间戳、区块编号等）是正确的，从而确保区块头的有效性
    @classmethod
    def validate_header(
            cls, header: BlockHeaderAPI, parent_header: BlockHeaderAPI
    ) -> None:
        # 检查父区块头是否存在。如果父区块头不存在，说明当前区块头是创世块的头
        if parent_header is None:
            # to validate genesis header, check if it equals canonical header
            # at block number 0
            raise ValidationError(
                "Must have access to parent header to validate current header"
            )
        else:
            # 对当前区块头的额外数据（extra_data）进行验证，确保其长度不超过预定义的最大字节数（cls.extra_data_max_bytes）
            validate_length_lte(
                header.extra_data,
                cls.extra_data_max_bytes,
                title="BlockHeader.extra_data",
            )

            # 验证当前区块头和父区块头的 gas 限制是否满足要求。
            cls.validate_gas(header, parent_header)

            # 检查当前区块头的区块编号是否连续递增。如果不是，抛出异常，因为区块的区块编号应该是连续的。
            if header.block_number != parent_header.block_number + 1:
                raise ValidationError(
                    "Blocks must be numbered consecutively. "
                    f"Block number #{header.block_number} "
                    f"has parent #{parent_header.block_number}"
                )

            # 检查当前区块头的时间戳是否严格晚于其父区块头的时间戳。
            if header.timestamp <= parent_header.timestamp:
                raise ValidationError(
                    "timestamp must be strictly later than parent, "
                    f"but is {parent_header.timestamp - header.timestamp} seconds before.\n"  # noqa: E501
                    f"- child  : {header.timestamp}\n"
                    f"- parent : {parent_header.timestamp}. "
                )

    # 方法主要是确保当前区块头的 gas 限制在合理的范围内，以维护区块链的一致性和安全性
    @classmethod
    def validate_gas(
            cls, header: BlockHeaderAPI, parent_header: BlockHeaderAPI
    ) -> None:
        validate_gas_limit(header.gas_limit, parent_header.gas_limit)

    # 确保当前的区块头满足共识规则，从而防止恶意或无效的区块进入区块链
    def validate_seal(self, header: BlockHeaderAPI) -> None:
        try:
            self._consensus.validate_seal(header)
        except ValidationError as exc:
            self.cls_logger.debug(
                "Failed to validate seal on header: %r. Error: %s",
                header.as_dict(),
                exc,
            )
            raise

    # 确保区块头的扩展部分满足共识规则中的额外验证要求
    def validate_seal_extension(
            self, header: BlockHeaderAPI, parents: Iterable[BlockHeaderAPI]
    ) -> None:
        self._consensus.validate_seal_extension(header, parents)

    # 确保叔块的区块头满足一定的规则和要求，以确保叔块的有效性，并防止潜在的错误或恶意叔块进入区块链
    @classmethod
    def validate_uncle(
            cls, block: BlockAPI, uncle: BlockHeaderAPI, uncle_parent: BlockHeaderAPI
    ) -> None:
        # 检查叔块的区块号是否大于或等于主区块的区块号
        if uncle.block_number >= block.number:
            raise ValidationError(
                f"Uncle number ({uncle.block_number}) is higher than "
                f"block number ({block.number})"
            )
        # 检查叔块的区块号是否是其父区块号加一
        if uncle.block_number != uncle_parent.block_number + 1:
            raise ValidationError(
                f"Uncle number ({uncle.block_number}) is not one above "
                f"ancestor's number ({uncle_parent.block_number})"
            )
        # 检查叔块的时间戳是否比其父区块的时间戳更新
        if uncle.timestamp <= uncle_parent.timestamp:
            raise ValidationError(
                f"Uncle timestamp ({uncle.timestamp}) is not newer than its "
                f"parent's timestamp ({uncle_parent.timestamp})"
            )
        # 检查叔块的 gas 使用量是否超过了 gas 限制
        if uncle.gas_used > uncle.gas_limit:
            raise ValidationError(
                f"Uncle's gas usage ({uncle.gas_used}) is above "
                f"the limit ({uncle.gas_limit})"
            )
        # 将叔块父区块的 gas 限制存储在 uncle_parent_gas_limit 变量中
        uncle_parent_gas_limit = uncle_parent.gas_limit
        # 检查是否存在 uncle_parent 区块的 base_fee_per_gas 属性（用于伯林硬分叉）并且叔块 uncle 有 base_fee_per_gas 属性
        if not hasattr(uncle_parent, "base_fee_per_gas") and hasattr(
                uncle, "base_fee_per_gas"
        ):
            # 如果满足条件，这表示发生了从伯林到伦敦的过渡，需要将叔块父区块的 gas 限制加倍，因为伦敦硬分叉后的规则要求这么做
            uncle_parent_gas_limit *= 2
        # 调用 validate_gas_limit 方法，验证叔块的 gas 限制是否符合规则
        validate_gas_limit(uncle.gas_limit, uncle_parent_gas_limit)

    # get_state_class 方法将返回一个实现了 StateAPI 接口的状态类
    @classmethod
    def get_state_class(cls) -> Type[StateAPI]:
        if cls._state_class is None:
            raise AttributeError("No `_state_class` has been set for this VM")

        return cls._state_class

    # in_costless_state 上下文管理器允许在虚拟状态下执行代码，以便模拟某些操作，而无需影响实际区块链状态。这对于执行特定类型的操作或测试非常有用，而不必担心实际状态的改变
    @contextlib.contextmanager
    def in_costless_state(self) -> Iterator[StateAPI]:
        # 在上下文开始之前，它获取当前区块头（header），通常是正在执行的区块的区块头
        header = self.get_header()

        # 创建了一个新的区块 temp_block。temp_block 的父区块是当前区块 header，并且使用与 header 相同的 coinbase 地址。这样可以创建一个包含与当前区块相同信息的虚拟区块。
        temp_block = self.generate_block_from_parent_header_and_coinbase(
            header, header.coinbase
        )

        # 在 prev_hashes 中构建了一个迭代器，其中包含了当前区块 header 的哈希，以及虚拟区块 temp_block 之前的一些哈希，通常是上一次执行的区块哈希。这些哈希将用于构建虚拟状态。
        prev_hashes = itertools.chain((header.hash,), self.previous_hashes)

        # 检查虚拟区块 temp_block 的区块头是否包含 base_fee_per_gas 属性（通常用于伯林硬分叉）
        if hasattr(temp_block.header, "base_fee_per_gas"):
            # 如果存在，就创建一个新的区块头 free_header，其 base_fee_per_gas 属性设置为 0。这是为了模拟无成本状态，其中 gas 费用设置为零
            free_header = temp_block.header.copy(base_fee_per_gas=0)
        else:
            # 如果虚拟区块的区块头不包含 base_fee_per_gas 属性，那么使用原始虚拟区块的区块头。
            free_header = temp_block.header

        # 基于上述构建的区块头、区块链上下文和哈希链，创建了一个新的虚拟状态 state。这个虚拟状态用于在无成本状态下执行代码。
        state = self.build_state(
            self.chaindb.db, free_header, self.chain_context, prev_hashes
        )

        # 在进入上下文之前，创建了虚拟状态 state 的快照，以便在退出上下文时能够还原状态。
        snapshot = state.snapshot()
        # 通过 yield 关键字将虚拟状态 state 提供给上下文中的代码块，允许在上下文中对状态进行读取和修改。
        yield state
        # 在上下文结束后，使用快照 snapshot 还原虚拟状态 state，以取消在上下文中对状态的任何修改。这确保了在无成本状态下执行的更改不会影响实际区块链状态。
        state.revert(snapshot)