from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from isa import ARG_OPCODES, INPUT_PORT_ADDR, OUTPUT_PORT_ADDR, Opcode, binary_to_opcode


WORD_MASK = 0xFFFFFFFF
SIGN_BIT = 0x80000000


class HardwareError(Exception):
    pass


def to_word(value: int) -> int:
    value &= WORD_MASK
    if value & SIGN_BIT:
        return value - (1 << 32)
    return value


def token_to_value(token: str) -> int:
    token = token.strip()
    if token == "":
        return ord(" ")
    try:
        return int(token, 0)
    except ValueError:
        return ord(token[0])


@dataclass
class LatchBatch:
    writes: list[tuple[str, Callable[[], None]]] = field(default_factory=list)

    def add(self, name: str, action: Callable[[], None]) -> None:
        self.writes.append((name, action))

    def commit(self) -> list[str]:
        names = [name for name, _ in self.writes]
        for _, action in self.writes:
            action()
        self.writes.clear()
        return names


class ALU:
    def __init__(self) -> None:
        self.left = 0
        self.right = 0
        self.out = 0
        self.flag_z = True
        self.flag_n = False

    def latch_left(self, value: int, batch: LatchBatch) -> None:
        batch.add("ALU.left", lambda value=value: setattr(self, "left", to_word(value)))

    def latch_right(self, value: int, batch: LatchBatch) -> None:
        batch.add("ALU.right", lambda value=value: setattr(self, "right", to_word(value)))

    def latch_flags_from_status(self, status: int, batch: LatchBatch) -> None:
        def commit() -> None:
            self.flag_n = bool((status >> 1) & 1)
            self.flag_z = bool(status & 1)

        batch.add("NZ", commit)

    def status(self) -> int:
        return (int(self.flag_n) << 1) | int(self.flag_z)

    def _set_out(self, value: int) -> None:
        self.out = to_word(value)
        self.flag_z = self.out == 0
        self.flag_n = self.out < 0

    def pass_left(self) -> None:
        self._set_out(self.left)

    def add(self) -> None:
        self._set_out(self.right + self.left)

    def sub(self) -> None:
        self._set_out(self.right - self.left)

    def mul(self) -> None:
        self._set_out(self.right * self.left)

    def div(self) -> None:
        self._set_out(0 if self.left == 0 else int(self.right / self.left))

    def mod(self) -> None:
        self._set_out(0 if self.left == 0 else self.right % self.left)

    def inc(self) -> None:
        self._set_out(self.left + 1)

    def dec(self) -> None:
        self._set_out(self.left - 1)

    def inv(self) -> None:
        self._set_out(~self.left)

    def bit_and(self) -> None:
        self._set_out(self.right & self.left)

    def bit_xor(self) -> None:
        self._set_out(self.right ^ self.left)

    def bit_or(self) -> None:
        self._set_out(self.right | self.left)

    def lshift(self) -> None:
        self._set_out(self.left << 1)

    def rshift(self) -> None:
        self._set_out(self.left >> 1)


class DataPath:
    def __init__(
        self,
        data_memory_size: int,
        input_port: int,
        output_port: int,
        stack_size: int = 256,
        return_stack_size: int = 256,
    ) -> None:
        self.alu = ALU()
        self.data_memory = [0] * data_memory_size
        self.data_stack = [0] * stack_size
        self.return_stack = [0] * return_stack_size
        self.sp = 0
        self.rsp = 0
        self.a = 0
        self.ar = 0
        self.input_port = input_port
        self.output_port = output_port
        self.input_port_value = 0
        self.output_buffer: list[int] = []
        self.memory_action = "-"

    def load_data_image(self, data_image: dict[str, int] | dict[int, int]) -> None:
        for addr, value in data_image.items():
            index = int(addr)
            self._check_data_addr(index)
            self.data_memory[index] = to_word(int(value))

    def latch_a(self, value: int, batch: LatchBatch) -> None:
        batch.add("A", lambda value=value: setattr(self, "a", to_word(value)))

    def latch_ar(self, value: int, batch: LatchBatch) -> None:
        batch.add("AR", lambda value=value: setattr(self, "ar", to_word(value)))

    def latch_input_port(self, value: int, batch: LatchBatch) -> None:
        batch.add("IN_PORT", lambda value=value: setattr(self, "input_port_value", to_word(value)))

    def push_data(self, value: int, batch: LatchBatch, name: str = "DS.push") -> None:
        sp = self.sp
        if sp >= len(self.data_stack):
            raise HardwareError("Data stack overflow")

        def commit() -> None:
            self.data_stack[sp] = to_word(value)
            self.sp = sp + 1

        batch.add(name, commit)

    def pop_data_now(self) -> int:
        if self.sp <= 0:
            raise HardwareError("Data stack underflow")
        self.sp -= 1
        return self.data_stack[self.sp]

    def dup_data(self, batch: LatchBatch) -> None:
        if self.sp <= 0:
            raise HardwareError("Data stack underflow")
        self.push_data(self.data_stack[self.sp - 1], batch, "DS.dup")

    def swap_top_two(self, batch: LatchBatch) -> None:
        if self.sp < 2:
            raise HardwareError("Data stack underflow")
        sp = self.sp
        top = self.data_stack[sp - 1]
        second = self.data_stack[sp - 2]

        def commit() -> None:
            self.data_stack[sp - 2] = top
            self.data_stack[sp - 1] = second

        batch.add("DS.over", commit)

    def push_return(self, value: int, batch: LatchBatch, name: str = "RS.push") -> None:
        rsp = self.rsp
        if rsp >= len(self.return_stack):
            raise HardwareError("Return stack overflow")

        def commit() -> None:
            self.return_stack[rsp] = to_word(value)
            self.rsp = rsp + 1

        batch.add(name, commit)

    def pop_return_now(self) -> int:
        if self.rsp <= 0:
            raise HardwareError("Return stack underflow")
        self.rsp -= 1
        return self.return_stack[self.rsp]

    def read_memory(self, addr: int) -> int:
        self.memory_action = f"read mem[{addr}]"
        if addr == self.input_port:
            return self.input_port_value
        if addr == self.output_port:
            raise HardwareError("Cannot read from output port")
        self._check_data_addr(addr)
        return self.data_memory[addr]

    def write_memory(self, addr: int, value: int, batch: LatchBatch) -> None:
        self.memory_action = f"write mem[{addr}] <- {to_word(value)}"
        if addr == self.input_port:
            raise HardwareError("Cannot write to input port")
        if addr == self.output_port:
            batch.add("OUT", lambda value=value: self.output_buffer.append(to_word(value)))
            return
        self._check_data_addr(addr)
        batch.add("MEM", lambda addr=addr, value=value: self.data_memory.__setitem__(addr, to_word(value)))

    def _check_data_addr(self, addr: int) -> None:
        if addr < 0 or addr >= len(self.data_memory):
            raise HardwareError(f"Data memory address out of range: {addr}")

    def data_stack_snapshot(self) -> list[int]:
        return self.data_stack[: self.sp]

    def return_stack_snapshot(self) -> list[int]:
        return self.return_stack[: self.rsp]


@dataclass
class MachineConfig:
    input_port: int
    output_port: int
    data_memory_size: int
    command_memory_size: int
    entry_point: int
    data_image: dict[str, int]

    @classmethod
    def load(cls, filename: str) -> MachineConfig:
        raw = json.loads(Path(filename).read_text(encoding="utf-8"))
        return cls(
            input_port=int(raw.get("input_port", INPUT_PORT_ADDR)),
            output_port=int(raw.get("output_port", OUTPUT_PORT_ADDR)),
            data_memory_size=int(raw.get("data_memory_size", 32000)),
            command_memory_size=int(raw.get("command_memory_size", 32000)),
            entry_point=int(raw.get("entry_point", 0)),
            data_image=raw.get("data_image", {}),
        )


class ControlUnit:
    def __init__(
        self,
        command_memory_size: int,
        data_path: DataPath,
        entry_point: int = 0,
        input_schedule: list[tuple[int, int]] | None = None,
    ) -> None:
        self.command_memory = [0] * command_memory_size
        self.program_size = 0
        self.dp = data_path
        self.pc = entry_point
        self.tick_no = 0
        self.step = 0
        self.ir_valid = False
        self.ir_opcode: Opcode | None = None
        self.ir_arg = 0
        self.ie = True
        self.pending_interrupt = False
        self.in_interrupt = False
        self.entering_interrupt = False
        self.int_step = 0
        self.input_schedule = sorted(input_schedule or [], key=lambda item: item[0])
        self.log_lines: list[str] = []

    def load_command_memory(self, binary_code: bytes) -> None:
        if len(binary_code) > len(self.command_memory):
            raise HardwareError("Command memory overflow")
        self.program_size = len(binary_code)
        for index, value in enumerate(binary_code):
            self.command_memory[index] = value

    def current_tick(self) -> int:
        return self.tick_no

    def process_next_tick(self) -> None:
        batch = LatchBatch()
        self.dp.memory_action = "-"
        action = self._process_tick(batch)
        committed = batch.commit()
        self._log(action, committed)
        self.tick_no += 1

    def _process_tick(self, batch: LatchBatch) -> str:
        if not self.ir_valid and self.step == 0 and not self.entering_interrupt:
            self._check_interrupt_schedule(batch)
        if not self.ir_valid and self.step == 0 and not self.entering_interrupt and self.pending_interrupt and self.ie:
            self.entering_interrupt = True
            self.int_step = 0
            self.pending_interrupt = False
            self.ie = False
            self.in_interrupt = True

        if self.entering_interrupt:
            return self._process_interrupt_entry(batch)

        if not self.ir_valid:
            return self._fetch_instruction(batch)

        if self.ir_opcode is Opcode.HALT:
            raise StopIteration
        if self.ir_opcode is None:
            raise HardwareError("Instruction register is empty")
        return self._execute_instruction(self.ir_opcode, self.ir_arg, batch)

    def _check_interrupt_schedule(self, batch: LatchBatch) -> None:
        while self.input_schedule and self.tick_no >= self.input_schedule[0][0]:
            _, value = self.input_schedule.pop(0)
            self.dp.latch_input_port(value, batch)
            self.pending_interrupt = True

    def _process_interrupt_entry(self, batch: LatchBatch) -> str:
        if self.int_step == 0:
            self.dp.push_return(self.pc, batch, "RS.push(PC)")
            self.int_step += 1
            return "INT ENTRY: push PC"
        if self.int_step == 1:
            self.dp.push_return(self.dp.alu.status(), batch, "RS.push(SR)")
            self.int_step += 1
            return "INT ENTRY: push SR"
        if self.int_step == 2:
            self.dp.push_return(self.dp.a, batch, "RS.push(A)")
            self.int_step += 1
            return "INT ENTRY: push A"
        if self.int_step == 3:
            self.dp.latch_ar(0, batch)
            self.int_step += 1
            return "INT ENTRY: AR <- 0"
        if self.int_step == 4:
            vector = self.dp.read_memory(0)
            self.dp.push_data(vector, batch, "DS.push(vector)")
            self.int_step += 1
            return "INT ENTRY: read vector"
        vector = self.dp.pop_data_now()
        batch.add("PC", lambda vector=vector: setattr(self, "pc", vector))
        self.entering_interrupt = False
        self.int_step = 0
        return "INT ENTRY: PC <- vector"

    def _fetch_instruction(self, batch: LatchBatch) -> str:
        if self.pc < 0 or self.pc >= self.program_size:
            raise HardwareError(f"Program counter out of loaded code range: {self.pc}")
        opcode_byte = self.command_memory[self.pc]
        if opcode_byte not in binary_to_opcode:
            raise HardwareError(f"Unknown opcode byte 0x{opcode_byte:02x} at PC {self.pc}")
        opcode = binary_to_opcode[opcode_byte]
        arg = 0
        if opcode in ARG_OPCODES:
            if self.pc + 5 > self.program_size:
                raise HardwareError(f"Truncated argument for {opcode.value} at PC {self.pc}")
            arg_bytes = bytes(self.command_memory[self.pc + 1 : self.pc + 5])
            arg = int.from_bytes(arg_bytes, byteorder="big", signed=True)
        batch.add("IR", lambda opcode=opcode, arg=arg: self._latch_ir(opcode, arg))
        return f"fetch {opcode.value}{f' {arg}' if opcode in ARG_OPCODES else ''}"

    def _latch_ir(self, opcode: Opcode, arg: int) -> None:
        self.ir_opcode = opcode
        self.ir_arg = arg
        self.ir_valid = True

    def _execute_instruction(self, opcode: Opcode, arg: int, batch: LatchBatch) -> str:
        if opcode is Opcode.LIT:
            self.dp.push_data(arg, batch)
            self._latch_next_pc(batch, opcode)
            return f"{opcode.value} {arg}"
        if opcode is Opcode.LOAD:
            return self._execute_load(arg, batch)
        if opcode is Opcode.STORE:
            return self._execute_store(arg, batch)
        if opcode is Opcode.TOA:
            value = self.dp.pop_data_now()
            self.dp.latch_a(value, batch)
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode is Opcode.TOSTACKFROMA:
            self.dp.push_data(self.dp.a, batch)
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode is Opcode.ALOAD:
            return self._execute_aload(batch, increment=False)
        if opcode is Opcode.ALOADP:
            return self._execute_aload(batch, increment=True)
        if opcode is Opcode.ASTORE:
            return self._execute_astore(batch)
        if opcode is Opcode.DROP:
            self.dp.pop_data_now()
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode is Opcode.DUP:
            self.dp.dup_data(batch)
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode is Opcode.OVER:
            self.dp.swap_top_two(batch)
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode in {
            Opcode.INC,
            Opcode.DEC,
            Opcode.ADD,
            Opcode.SUB,
            Opcode.MUL,
            Opcode.DIV,
            Opcode.MOD,
            Opcode.INV,
            Opcode.AND,
            Opcode.XOR,
            Opcode.OR,
            Opcode.LSHIFT,
            Opcode.RSHIFT,
        }:
            return self._execute_alu(opcode, batch)
        if opcode is Opcode.JMP:
            self._latch_pc(batch, arg)
            return f"{opcode.value} {arg}"
        if opcode is Opcode.IF:
            return self._execute_conditional(opcode, arg, batch, condition=lambda: self.dp.alu.flag_z)
        if opcode is Opcode.MIF:
            return self._execute_conditional(opcode, arg, batch, condition=lambda: not self.dp.alu.flag_n)
        if opcode is Opcode.NIF:
            return self._execute_conditional(opcode, arg, batch, condition=lambda: not self.dp.alu.flag_z)
        if opcode is Opcode.CALL:
            return_address = self.pc + 5
            self.dp.push_return(return_address, batch, "RS.push(ret)")
            self._latch_pc(batch, arg)
            return f"{opcode.value} {arg}"
        if opcode is Opcode.RET:
            address = self.dp.pop_return_now()
            self._latch_pc(batch, address)
            return opcode.value
        if opcode is Opcode.RINTOT:
            value = self.dp.pop_return_now()
            self.dp.push_data(value, batch)
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode is Opcode.TINTOR:
            value = self.dp.pop_data_now()
            self.dp.push_return(value, batch)
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode is Opcode.EI:
            batch.add("IE", lambda: setattr(self, "ie", True))
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode is Opcode.DI:
            batch.add("IE", lambda: setattr(self, "ie", False))
            self._latch_next_pc(batch, opcode)
            return opcode.value
        if opcode is Opcode.IRET:
            return self._execute_iret(batch)
        raise HardwareError(f"Opcode not implemented: {opcode}")

    def _execute_load(self, arg: int, batch: LatchBatch) -> str:
        if self.step == 0:
            self.dp.latch_ar(arg, batch)
            self.step = 1
            return f"load.addr {arg}"
        value = self.dp.read_memory(self.dp.ar)
        self.dp.push_data(value, batch)
        self.step = 0
        self._latch_next_pc(batch, Opcode.LOAD)
        return "load.read"

    def _execute_store(self, arg: int, batch: LatchBatch) -> str:
        if self.step == 0:
            self.dp.latch_ar(arg, batch)
            self.step = 1
            return f"store.addr {arg}"
        value = self.dp.pop_data_now()
        self.dp.write_memory(self.dp.ar, value, batch)
        self.step = 0
        self._latch_next_pc(batch, Opcode.STORE)
        return "store.write"

    def _execute_aload(self, batch: LatchBatch, increment: bool) -> str:
        if self.step == 0:
            self.dp.latch_ar(self.dp.a, batch)
            self.step = 1
            return "aload.addr"
        if self.step == 1:
            value = self.dp.read_memory(self.dp.ar)
            self.dp.push_data(value, batch)
            if increment:
                self.step = 2
            else:
                self.step = 0
                self._latch_next_pc(batch, Opcode.ALOAD)
            return "aload.read"
        self.dp.latch_a(self.dp.a + 1, batch)
        self.step = 0
        self._latch_next_pc(batch, Opcode.ALOADP)
        return "aload.increment"

    def _execute_astore(self, batch: LatchBatch) -> str:
        if self.step == 0:
            self.dp.latch_ar(self.dp.a, batch)
            self.step = 1
            return "astore.addr"
        value = self.dp.pop_data_now()
        self.dp.write_memory(self.dp.ar, value, batch)
        self.step = 0
        self._latch_next_pc(batch, Opcode.ASTORE)
        return "astore.write"

    def _execute_alu(self, opcode: Opcode, batch: LatchBatch) -> str:
        if self.step == 0:
            left = self.dp.pop_data_now()
            self.dp.alu.latch_left(left, batch)
            if opcode in {Opcode.ADD, Opcode.SUB, Opcode.MUL, Opcode.DIV, Opcode.MOD, Opcode.AND, Opcode.XOR, Opcode.OR}:
                right = self.dp.pop_data_now()
                self.dp.alu.latch_right(right, batch)
            self.step = 1
            return f"{opcode.value}.load_operands"
        operations = {
            Opcode.INC: self.dp.alu.inc,
            Opcode.DEC: self.dp.alu.dec,
            Opcode.ADD: self.dp.alu.add,
            Opcode.SUB: self.dp.alu.sub,
            Opcode.MUL: self.dp.alu.mul,
            Opcode.DIV: self.dp.alu.div,
            Opcode.MOD: self.dp.alu.mod,
            Opcode.INV: self.dp.alu.inv,
            Opcode.AND: self.dp.alu.bit_and,
            Opcode.XOR: self.dp.alu.bit_xor,
            Opcode.OR: self.dp.alu.bit_or,
            Opcode.LSHIFT: self.dp.alu.lshift,
            Opcode.RSHIFT: self.dp.alu.rshift,
        }
        operations[opcode]()
        self.dp.push_data(self.dp.alu.out, batch, "DS.push(ALU)")
        self.step = 0
        self._latch_next_pc(batch, opcode)
        return f"{opcode.value}.write_result"

    def _execute_conditional(self, opcode: Opcode, arg: int, batch: LatchBatch, condition: Callable[[], bool]) -> str:
        if self.step == 0:
            value = self.dp.pop_data_now()
            self.dp.alu.latch_left(value, batch)
            self.step = 1
            return f"{opcode.value}.test"
        self.dp.alu.pass_left()
        if condition():
            self._latch_pc(batch, arg)
        else:
            self._latch_next_pc(batch, opcode)
        self.step = 0
        return f"{opcode.value}.branch"

    def _execute_iret(self, batch: LatchBatch) -> str:
        if self.step == 0:
            address_register = self.dp.pop_return_now()
            self.dp.latch_a(address_register, batch)
            self.step = 1
            return "iret.restore_a"
        if self.step == 1:
            status = self.dp.pop_return_now()
            self.dp.alu.latch_flags_from_status(status, batch)
            self.step = 2
            return "iret.restore_status"
        address = self.dp.pop_return_now()
        self._latch_pc(batch, address)
        batch.add("IE", lambda: setattr(self, "ie", True))
        batch.add("IN_INTR", lambda: setattr(self, "in_interrupt", False))
        self.step = 0
        return "iret.restore_pc"

    def _latch_pc(self, batch: LatchBatch, address: int) -> None:
        batch.add("PC", lambda address=address: setattr(self, "pc", address))
        batch.add("IR.clear", self._clear_ir)

    def _latch_next_pc(self, batch: LatchBatch, opcode: Opcode) -> None:
        next_pc = self.pc + (5 if opcode in ARG_OPCODES else 1)
        self._latch_pc(batch, next_pc)

    def _clear_ir(self) -> None:
        self.ir_valid = False

    def _log(self, action: str, committed: list[str]) -> None:
        opcode = self.ir_opcode.value if self.ir_opcode and not action.startswith("INT ENTRY") else "----"
        if self.ir_opcode and not action.startswith("INT ENTRY") and self.ir_opcode in ARG_OPCODES:
            opcode = f"{opcode} {self.ir_arg}"
        output = self._format_output()
        line = (
            f"TICK: {self.tick_no:06d} | PC: {self.pc:05d} | STEP: {self.step} | "
            f"IR: {opcode} | A: {self.dp.a} | AR: {self.dp.ar} | NZ: {int(self.dp.alu.flag_n)}{int(self.dp.alu.flag_z)} | "
            f"IE: {int(self.ie)} | PENDING: {int(self.pending_interrupt)} | IN_INTR: {int(self.in_interrupt)} | "
            f"DS: {self.dp.data_stack_snapshot()} | RS: {self.dp.return_stack_snapshot()} | "
            f"MEM: {self.dp.memory_action} | LATCH: {','.join(committed) or '-'} | OUT: {output} | {action}"
        )
        self.log_lines.append(line)

    def _format_output(self) -> str:
        text = ""
        for value in self.dp.output_buffer:
            if value > 255 or value < 0:
                text += f"{value} "
            elif value == 10:
                text += "\n"
            elif 32 <= value <= 126:
                text += chr(value)
            else:
                text += f"\\x{value:02x}"
        return repr(text)


def parse_schedule(filename: str | None) -> list[tuple[int, int]]:
    if filename is None:
        return []
    schedule: list[tuple[int, int]] = []
    for raw_line in Path(filename).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tick_text, _, token = line.partition(" ")
        if token == "" and raw_line.endswith(" "):
            token = " "
        elif token == "":
            token = "10"
        schedule.append((int(tick_text), token_to_value(token)))
    return schedule


def format_output(output_buffer: list[int]) -> str:
    text = ""
    for value in output_buffer:
        if value > 255 or value < 0:
            text += f"{value} "
        elif value == 10:
            text += "\n"
        elif 32 <= value <= 126:
            text += chr(value)
        else:
            text += f"\\x{value:02x}"
    return text


def run_cpu(
    code_file: str,
    config_file: str,
    schedule_file: str | None = None,
    log_file: str | None = None,
    limit: int = 1_000_000,
) -> tuple[str, int]:
    config = MachineConfig.load(config_file)
    binary_code = Path(code_file).read_bytes()
    data_path = DataPath(config.data_memory_size, config.input_port, config.output_port)
    data_path.load_data_image(config.data_image)
    control_unit = ControlUnit(
        config.command_memory_size,
        data_path,
        entry_point=config.entry_point,
        input_schedule=parse_schedule(schedule_file),
    )
    control_unit.load_command_memory(binary_code)

    try:
        while control_unit.current_tick() < limit:
            control_unit.process_next_tick()
    except StopIteration:
        pass
    else:
        raise HardwareError(f"Tick limit exceeded: {limit}")

    if log_file:
        Path(log_file).write_text("\n".join(control_unit.log_lines) + "\n", encoding="utf-8")
    return format_output(data_path.output_buffer), control_unit.current_tick()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sleekstack-machine",
        description="Run SleekStack binary code on a tick-accurate stack processor model.",
    )
    parser.add_argument("code_file")
    parser.add_argument("config_file")
    parser.add_argument("schedule_file", nargs="?")
    parser.add_argument("log_file", nargs="?")
    parser.add_argument("--log", dest="named_log_file")
    parser.add_argument("--limit", type=int, default=1_000_000)
    args = parser.parse_args()
    schedule_file = args.schedule_file
    log_file = args.named_log_file or args.log_file
    if args.named_log_file is None and args.log_file is None and schedule_file:
        suffix = Path(schedule_file).suffix.lower()
        if suffix in {".log", ".trace"}:
            log_file = schedule_file
            schedule_file = None

    try:
        output, ticks = run_cpu(
            args.code_file,
            args.config_file,
            schedule_file=schedule_file,
            log_file=log_file,
            limit=args.limit,
        )
    except HardwareError as error:
        parser.exit(1, f"machine error: {error}\n")

    print(output)
    print(f"ticks: {ticks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
