from eth.abc import (
    ComputationAPI,
)


def mstore(computation: ComputationAPI) -> None:
    # 从执行上下文的栈中弹出一个整数，表示要将数据存储到内存的起始位置
    start_position = computation.stack_pop1_int()
    # 接着从执行上下文的栈中弹出一个字节数组，这是要存储在内存中的值
    value = computation.stack_pop1_bytes()
    # 将弹出的字节数组 value 右填充（如果长度不足 32 字节）到 32 字节的长度，填充使用的字节是零字节
    padded_value = value.rjust(32, b"\x00")
    # 从填充后的 padded_value 中取最后的 32 字节，确保数据长度为 32 字节
    normalized_value = padded_value[-32:]
    # 告诉执行上下文，要扩展内存以容纳 32 字节的数据，如果需要
    computation.extend_memory(start_position, 32)
    # 将 normalized_value 写入到指定的内存位置 start_position，写入的数据长度为 32 字节
    
    computation.memory_write(start_position, 32, normalized_value)


def mstore8(computation: ComputationAPI) -> None:
    start_position = computation.stack_pop1_int()
    value = computation.stack_pop1_bytes()

    padded_value = value.rjust(1, b"\x00")
    normalized_value = padded_value[-1:]

    computation.extend_memory(start_position, 1)

    computation.memory_write(start_position, 1, normalized_value)


def mload(computation: ComputationAPI) -> None:
    start_position = computation.stack_pop1_int()

    computation.extend_memory(start_position, 32)

    value = computation.memory_read_bytes(start_position, 32)
    computation.stack_push_bytes(value)


def msize(computation: ComputationAPI) -> None:
    computation.stack_push_int(len(computation._memory))
