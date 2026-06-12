from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


INPUT_PORT_ADDR = 31998
OUTPUT_PORT_ADDR = 31999


class Opcode(str, Enum):
    INC = "increment"
    DEC = "decrement"
    SUB = "sub"
    ADD = "add"
    MUL = "mul"
    DIV = "div"
    MOD = "mod"

    LIT = "literal"
    TOA = "stack_to_a"
    TOSTACKFROMA = "a_to_stack"
    ASTORE = "a_store"
    ALOAD = "a_load"
    LOAD = "load"
    STORE = "store"

    LSHIFT = "lshift"
    RSHIFT = "rshift"
    INV = "inv"
    AND = "and"
    XOR = "xor"
    OR = "or"

    DROP = "drop"
    DUP = "dup"
    OVER = "over"

    JMP = "jmp"
    CALL = "call"
    RET = "return"
    IF = "if"
    MIF = "mif"
    NIF = "nif"

    RINTOT = "r_to_top"
    TINTOR = "top_to_r"
    ALOADP = "a_load_+"

    IRET = "iret"
    EI = "ei"
    DI = "di"
    HALT = "halt"

    def __str__(self) -> str:
        return self.value


opcode_to_binary = {
    Opcode.INC: 0x00,
    Opcode.DEC: 0x01,
    Opcode.SUB: 0x02,
    Opcode.ADD: 0x03,
    Opcode.MUL: 0x04,
    Opcode.DIV: 0x05,
    Opcode.LIT: 0x06,
    Opcode.TOA: 0x07,
    Opcode.TOSTACKFROMA: 0x09,
    Opcode.ASTORE: 0x0C,
    Opcode.ALOAD: 0x0E,
    Opcode.LOAD: 0x0F,
    Opcode.LSHIFT: 0x10,
    Opcode.RSHIFT: 0x11,
    Opcode.INV: 0x12,
    Opcode.AND: 0x13,
    Opcode.XOR: 0x14,
    Opcode.OR: 0x15,
    Opcode.DROP: 0x16,
    Opcode.DUP: 0x17,
    Opcode.OVER: 0x18,
    Opcode.CALL: 0x19,
    Opcode.RET: 0x1A,
    Opcode.IF: 0x1B,
    Opcode.MIF: 0x1C,
    Opcode.RINTOT: 0x1D,
    Opcode.TINTOR: 0x1E,
    Opcode.STORE: 0x1F,
    Opcode.JMP: 0x20,
    Opcode.ALOADP: 0x21,
    Opcode.IRET: 0x22,
    Opcode.EI: 0x23,
    Opcode.DI: 0x24,
    Opcode.NIF: 0x25,
    Opcode.MOD: 0x26,
    Opcode.HALT: 0xFF,
}

binary_to_opcode = {value: opcode for opcode, value in opcode_to_binary.items()}

ARG_OPCODES = {
    Opcode.LIT,
    Opcode.LOAD,
    Opcode.STORE,
    Opcode.JMP,
    Opcode.CALL,
    Opcode.IF,
    Opcode.MIF,
    Opcode.NIF,
}


@dataclass
class Instruction:
    opcode: Opcode
    arg: int | None = None

    @property
    def size(self) -> int:
        return 5 if self.opcode in ARG_OPCODES else 1

    def to_bytes(self) -> bytes:
        output = bytearray([opcode_to_binary[self.opcode]])
        if self.opcode in ARG_OPCODES:
            if self.arg is None:
                raise ValueError(f"{self.opcode.value} requires an argument")
            output.extend(int(self.arg).to_bytes(4, byteorder="big", signed=True))
        elif self.arg is not None:
            raise ValueError(f"{self.opcode.value} does not accept an argument")
        return bytes(output)

    def mnemonic(self) -> str:
        if self.arg is None:
            return self.opcode.value
        return f"{self.opcode.value} {self.arg}"


def to_bytes(code: list[Instruction]) -> bytes:
    return b"".join(instruction.to_bytes() for instruction in code)


def to_hex(code: list[Instruction]) -> str:
    return to_bytes(code).hex()


def from_bytes(binary_code: bytes) -> list[Instruction]:
    code: list[Instruction] = []
    pos = 0
    while pos < len(binary_code):
        if binary_code[pos] not in binary_to_opcode:
            raise ValueError(f"Unknown opcode byte 0x{binary_code[pos]:02x} at offset {pos}")
        opcode = binary_to_opcode[binary_code[pos]]
        pos += 1
        arg = None
        if opcode in ARG_OPCODES:
            if pos + 4 > len(binary_code):
                raise ValueError(f"Truncated argument for {opcode.value} at offset {pos - 1}")
            arg = int.from_bytes(binary_code[pos : pos + 4], byteorder="big", signed=True)
            pos += 4
        code.append(Instruction(opcode, arg))
    return code


def listing(code: list[Instruction], data_image: dict[int, int]) -> str:
    lines = ["---------data_memory_pos-32-bit---"]
    for address in sorted(data_image):
        lines.append(f"0x{address:04x} - 0x{data_image[address] & 0xFFFFFFFF:08x} - {data_image[address]}")
    lines.append("---------command_memory---8-bit---")
    lines.append("<address> - <HEXCODE> - <mnemonic>")
    pc = 0
    for instruction in code:
        raw = instruction.to_bytes()
        end = pc + len(raw) - 1
        if len(raw) == 1:
            lines.append(f"0x{pc:04x} - 0x{raw.hex()} - {instruction.mnemonic()}")
        else:
            lines.append(f"0x{pc:04x}-0x{end:04x} - 0x{raw.hex()} - {instruction.mnemonic()}")
        pc += len(raw)
    return "\n".join(lines) + "\n"
