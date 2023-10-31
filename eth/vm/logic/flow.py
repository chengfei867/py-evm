from eth.abc import (
    ComputationAPI,
)
from eth.exceptions import (
    Halt,
    InvalidInstruction,
    InvalidJumpDestination,
)
from eth.vm.opcode_values import (
    JUMPDEST,
)


def stop(computation: ComputationAPI) -> None:
    raise Halt("STOP")


def jump(computation: ComputationAPI) -> None:
    jump_dest = computation.stack_pop1_int()

    computation.code.program_counter = jump_dest

    next_opcode = computation.code.peek()

    if next_opcode != JUMPDEST:
        raise InvalidJumpDestination("Invalid Jump Destination")

    if not computation.code.is_valid_opcode(jump_dest):
        raise InvalidInstruction("Jump resulted in invalid instruction")


def jumpi(computation: ComputationAPI) -> None:
    # 从执行上下文的栈中弹出两个整数，分别表示跳转目标位置 jump_dest 和条件检查的值 check_value
    jump_dest, check_value = computation.stack_pop_ints(2)

    # 检查 check_value 是否为真（非零）。如果 check_value 为真，说明跳转条件成立
    if check_value:
        # 如果条件成立，将代码的程序计数器（program_counter）设置为 jump_dest，即跳转到指定的代码位置
        computation.code.program_counter = jump_dest

        # 在跳转后，这行代码用于查看下一个操作码（指令）
        # 这是因为 EVM 中的跳转必须跳转到有效的 JUMPDEST 操作码，否则会抛出异常
        next_opcode = computation.code.peek()

        # 检查下一个操作码是否为 JUMPDEST，JUMPDEST 是用于标记有效跳转目标位置的操作码
        if next_opcode != JUMPDEST:
            raise InvalidJumpDestination("Invalid Jump Destination")

        # 检查 jump_dest 是否是有效的操作码（指令），
        # 如果不是，抛出 InvalidInstruction 异常，表示跳转导致了无效的指令
        if not computation.code.is_valid_opcode(jump_dest):
            raise InvalidInstruction("Jump resulted in invalid instruction")


def jumpdest(computation: ComputationAPI) -> None:
    pass


def program_counter(computation: ComputationAPI) -> None:
    pc = max(computation.code.program_counter - 1, 0)

    computation.stack_push_int(pc)


def gas(computation: ComputationAPI) -> None:
    gas_remaining = computation.get_gas_remaining()

    computation.stack_push_int(gas_remaining)
