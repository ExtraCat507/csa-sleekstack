from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from isa import INPUT_PORT_ADDR, OUTPUT_PORT_ADDR, Instruction, Opcode, listing, to_bytes


class CompileError(Exception):
    pass


TOKEN_TYPES = [
    ("COMMENT", r"//.*"),
    ("STRING", r'"(?:[^"\\]|\\.)*"'),
    ("NUMBER", r"\d+"),
    ("DEF", r"\bdef\b"),
    ("LET", r"\blet\b"),
    ("IF", r"\bif\b"),
    ("ELSE", r"\belse\b"),
    ("WHILE", r"\bwhile\b"),
    ("RETURN", r"\breturn\b"),
    ("EQ_EQ", r"=="),
    ("BANG_EQ", r"!="),
    ("LESS_EQ", r"<="),
    ("GREATER_EQ", r">="),
    ("ASSIGN", r"="),
    ("LESS", r"<"),
    ("GREATER", r">"),
    ("PLUS", r"\+"),
    ("MINUS", r"-"),
    ("MUL", r"\*"),
    ("DIV", r"/"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("LBRACE", r"\{"),
    ("RBRACE", r"\}"),
    ("COMMA", r","),
    ("SEMICOLON", r";"),
    ("IDENT", r"[a-zA-Z_]\w*"),
    ("WHITESPACE", r"\s+"),
    ("MISMATCH", r"."),
]


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int


def lex(source_code: str) -> list[Token]:
    tokens: list[Token] = []
    line_num = 1
    tok_regex = "|".join("(?P<%s>%s)" % pair for pair in TOKEN_TYPES)
    for match in re.finditer(tok_regex, source_code):
        kind = match.lastgroup
        if kind is None:
            raise CompileError(f"Lexer failed at line {line_num}")
        value = match.group(kind)
        if kind in {"WHITESPACE", "COMMENT"}:
            line_num += value.count("\n")
            continue
        if kind == "MISMATCH":
            raise CompileError(f"Unexpected character {value!r} at line {line_num}")
        tokens.append(Token(kind, value, line_num))
        line_num += value.count("\n")
    tokens.append(Token("EOF", "", line_num))
    return tokens


class ASTNode:
    def pretty(self, indent: int = 0) -> str:
        raise NotImplementedError


@dataclass
class Program(ASTNode):
    decls: list[ASTNode]

    def pretty(self, indent: int = 0) -> str:
        return "".join(["  " * indent + "Program\n", *(d.pretty(indent + 1) for d in self.decls)])


@dataclass
class Function(ASTNode):
    name: str
    params: list[str]
    body: ASTNode

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + f"Function({self.name}, {self.params})\n" + self.body.pretty(indent + 1)


@dataclass
class Block(ASTNode):
    stmts: list[ASTNode]

    def pretty(self, indent: int = 0) -> str:
        return "".join(["  " * indent + "Block\n", *(s.pretty(indent + 1) for s in self.stmts)])


@dataclass
class Let(ASTNode):
    name: str
    expr: ASTNode | None

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + f"Let({self.name})\n" + (self.expr.pretty(indent + 1) if self.expr else "")


@dataclass
class Assign(ASTNode):
    name: str
    expr: ASTNode

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + f"Assign({self.name})\n" + self.expr.pretty(indent + 1)


@dataclass
class If(ASTNode):
    cond: ASTNode
    then_b: ASTNode
    else_b: ASTNode | None

    def pretty(self, indent: int = 0) -> str:
        text = "  " * indent + "If\n"
        text += "  " * (indent + 1) + "Cond:\n" + self.cond.pretty(indent + 2)
        text += "  " * (indent + 1) + "Then:\n" + self.then_b.pretty(indent + 2)
        if self.else_b:
            text += "  " * (indent + 1) + "Else:\n" + self.else_b.pretty(indent + 2)
        return text


@dataclass
class While(ASTNode):
    cond: ASTNode
    body: ASTNode

    def pretty(self, indent: int = 0) -> str:
        return (
            "  " * indent
            + "While\n"
            + "  " * (indent + 1)
            + "Cond:\n"
            + self.cond.pretty(indent + 2)
            + "  " * (indent + 1)
            + "Body:\n"
            + self.body.pretty(indent + 2)
        )


@dataclass
class Return(ASTNode):
    expr: ASTNode | None

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + "Return\n" + (self.expr.pretty(indent + 1) if self.expr else "")


@dataclass
class Call(ASTNode):
    name: str
    args: list[ASTNode]

    def pretty(self, indent: int = 0) -> str:
        return "".join(["  " * indent + f"Call({self.name})\n", *(a.pretty(indent + 1) for a in self.args)])


@dataclass
class Binary(ASTNode):
    left: ASTNode
    op: str
    right: ASTNode

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + f"Binary({self.op})\n" + self.left.pretty(indent + 1) + self.right.pretty(indent + 1)


@dataclass
class Unary(ASTNode):
    op: str
    expr: ASTNode

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + f"Unary({self.op})\n" + self.expr.pretty(indent + 1)


@dataclass
class Var(ASTNode):
    name: str

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + f"Var({self.name})\n"


@dataclass
class IntLiteral(ASTNode):
    value: int

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + f"IntLiteral({self.value})\n"


@dataclass
class StringLiteral(ASTNode):
    value: str

    def pretty(self, indent: int = 0) -> str:
        return "  " * indent + f"StringLiteral({self.value!r})\n"


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        token = self.peek()
        if token.kind != "EOF":
            self.pos += 1
        return token

    def match(self, *kinds: str) -> bool:
        if self.peek().kind in kinds:
            self.advance()
            return True
        return False

    def expect(self, kind: str, message: str) -> Token:
        if self.peek().kind == kind:
            return self.advance()
        token = self.peek()
        raise CompileError(f"{message} at line {token.line}, got {token.kind} ({token.value!r})")

    def parse(self) -> Program:
        decls: list[ASTNode] = []
        while self.peek().kind != "EOF":
            decls.append(self.declaration())
        return Program(decls)

    def declaration(self) -> ASTNode:
        if self.match("DEF"):
            return self.function_decl()
        if self.match("LET"):
            return self.var_decl()
        token = self.peek()
        raise CompileError(f"Expected declaration at line {token.line}, got {token.kind}")

    def function_decl(self) -> Function:
        name = self.expect("IDENT", "Expected function name").value
        self.expect("LPAREN", "Expected '(' after function name")
        params: list[str] = []
        if self.peek().kind != "RPAREN":
            params.append(self.expect("IDENT", "Expected parameter name").value)
            while self.match("COMMA"):
                params.append(self.expect("IDENT", "Expected parameter name").value)
        self.expect("RPAREN", "Expected ')' after parameters")
        return Function(name, params, self.block())

    def var_decl(self) -> Let:
        name = self.expect("IDENT", "Expected variable name").value
        expr = self.expression() if self.match("ASSIGN") else None
        self.expect("SEMICOLON", "Expected ';' after variable declaration")
        return Let(name, expr)

    def statement(self) -> ASTNode:
        if self.match("LET"):
            return self.var_decl()
        if self.peek().kind == "LBRACE":
            return self.block()
        if self.match("IF"):
            return self.if_stmt()
        if self.match("WHILE"):
            return self.while_stmt()
        if self.match("RETURN"):
            return self.return_stmt()
        return self.expr_stmt()

    def block(self) -> Block:
        self.expect("LBRACE", "Expected '{'")
        stmts: list[ASTNode] = []
        while self.peek().kind not in {"RBRACE", "EOF"}:
            stmts.append(self.statement())
        self.expect("RBRACE", "Expected '}'")
        return Block(stmts)

    def if_stmt(self) -> If:
        self.expect("LPAREN", "Expected '(' after if")
        cond = self.expression()
        self.expect("RPAREN", "Expected ')' after condition")
        then_b = self.statement()
        else_b = self.statement() if self.match("ELSE") else None
        return If(cond, then_b, else_b)

    def while_stmt(self) -> While:
        self.expect("LPAREN", "Expected '(' after while")
        cond = self.expression()
        self.expect("RPAREN", "Expected ')' after condition")
        return While(cond, self.statement())

    def return_stmt(self) -> Return:
        expr = None if self.peek().kind == "SEMICOLON" else self.expression()
        self.expect("SEMICOLON", "Expected ';' after return")
        return Return(expr)

    def expr_stmt(self) -> ASTNode:
        expr = self.expression()
        self.expect("SEMICOLON", "Expected ';' after expression")
        return expr

    def expression(self) -> ASTNode:
        return self.assignment()

    def assignment(self) -> ASTNode:
        expr = self.equality()
        if self.match("ASSIGN"):
            if not isinstance(expr, Var):
                raise CompileError("Invalid assignment target")
            return Assign(expr.name, self.assignment())
        return expr

    def equality(self) -> ASTNode:
        expr = self.comparison()
        while self.peek().kind in {"EQ_EQ", "BANG_EQ"}:
            expr = Binary(expr, self.advance().value, self.comparison())
        return expr

    def comparison(self) -> ASTNode:
        expr = self.term()
        while self.peek().kind in {"LESS", "LESS_EQ", "GREATER", "GREATER_EQ"}:
            expr = Binary(expr, self.advance().value, self.term())
        return expr

    def term(self) -> ASTNode:
        expr = self.factor()
        while self.peek().kind in {"PLUS", "MINUS"}:
            expr = Binary(expr, self.advance().value, self.factor())
        return expr

    def factor(self) -> ASTNode:
        expr = self.unary()
        while self.peek().kind in {"MUL", "DIV"}:
            expr = Binary(expr, self.advance().value, self.unary())
        return expr

    def unary(self) -> ASTNode:
        if self.match("MINUS"):
            return Unary("-", self.unary())
        return self.call()

    def call(self) -> ASTNode:
        expr = self.primary()
        while self.match("LPAREN"):
            args: list[ASTNode] = []
            if self.peek().kind != "RPAREN":
                args.append(self.expression())
                while self.match("COMMA"):
                    args.append(self.expression())
            self.expect("RPAREN", "Expected ')' after arguments")
            if not isinstance(expr, Var):
                raise CompileError("Can only call functions by name")
            expr = Call(expr.name, args)
        return expr

    def primary(self) -> ASTNode:
        token = self.advance()
        if token.kind == "NUMBER":
            return IntLiteral(int(token.value))
        if token.kind == "STRING":
            return StringLiteral(decode_string(token.value, token.line))
        if token.kind == "IDENT":
            return Var(token.value)
        if token.kind == "LPAREN":
            expr = self.expression()
            self.expect("RPAREN", "Expected ')'")
            return expr
        raise CompileError(f"Expected expression at line {token.line}, got {token.kind} ({token.value!r})")


def decode_string(token_value: str, line: int) -> str:
    raw = token_value[1:-1]
    escapes = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", '"': '"', "\\": "\\"}
    result: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i] != "\\":
            result.append(raw[i])
            i += 1
            continue
        i += 1
        if i >= len(raw) or raw[i] not in escapes:
            raise CompileError(f"Unsupported string escape at line {line}")
        result.append(escapes[raw[i]])
        i += 1
    return "".join(result)


class AbstractSyntaxTree:
    def __init__(self, filename_or_code: str, is_file: bool = True):
        code = Path(filename_or_code).read_text(encoding="utf-8") if is_file else filename_or_code
        self.program = Parser(lex(code)).parse()
        self.symbols: dict[str, str] = {}
        self._analyze()

    def _analyze(self) -> None:
        for decl in self.program.decls:
            if isinstance(decl, Function):
                kind = "function"
            elif isinstance(decl, Let):
                kind = "global"
            else:
                continue
            name = decl.name
            if name in self.symbols:
                raise CompileError(f"Duplicate top-level symbol: {name}")
            self.symbols[name] = kind

    def get_ast_dump(self) -> str:
        return self.program.pretty()


@dataclass
class FunctionLayout:
    params: list[str]
    locals: dict[str, int]


class CodeGen:
    BUILTINS = {"print", "putc", "puts", "getc", "ei", "di", "iret"}

    def __init__(self, ast: AbstractSyntaxTree):
        self.ast = ast
        self.code: list[Instruction] = []
        self.data_image: dict[int, int] = {0: 0}
        self.next_data_addr = 1
        self.strings: dict[str, int] = {}
        self.globals: dict[str, int] = {}
        self.functions: dict[str, int] = {}
        self.layouts: dict[str, FunctionLayout] = {}
        self.current_layout: FunctionLayout | None = None
        self.unresolved_calls: list[tuple[int, str]] = []
        self.unresolved_jumps: list[tuple[int, str]] = []

    def generate(self) -> None:
        self.allocate_data()
        self.emit(Opcode.JMP, 0)
        startup_addr = self.current_pc()
        self.code[0].arg = startup_addr
        self.generate_global_initializers()
        self.generate_call_by_name("main", [])
        self.emit(Opcode.HALT)

        for decl in self.ast.program.decls:
            if isinstance(decl, Function):
                self.generate_function(decl)

        self.patch_calls()
        self.data_image[0] = self.functions.get("trap", 0)

    def emit(self, opcode: Opcode, arg: int | None = None) -> int:
        self.code.append(Instruction(opcode, arg))
        return len(self.code) - 1

    def current_pc(self) -> int:
        return sum(instruction.size for instruction in self.code)

    def patch_instruction(self, index: int, arg: int) -> None:
        self.code[index].arg = arg

    def allocate_data(self) -> None:
        for decl in self.ast.program.decls:
            if isinstance(decl, Let):
                self.globals[decl.name] = self.alloc_word(0)
            elif isinstance(decl, Function):
                locals_map: dict[str, int] = {}
                for param in decl.params:
                    if param in locals_map:
                        raise CompileError(f"Duplicate parameter in function {decl.name}: {param}")
                    locals_map[param] = self.alloc_word(0)
                self.collect_function_locals(decl.body, locals_map)
                self.layouts[decl.name] = FunctionLayout(decl.params, locals_map)
                self.collect_strings(decl.body)

    def collect_function_locals(self, node: ASTNode, locals_map: dict[str, int]) -> None:
        if isinstance(node, Block):
            for stmt in node.stmts:
                self.collect_function_locals(stmt, locals_map)
        elif isinstance(node, Let):
            if node.name in locals_map:
                raise CompileError(f"Duplicate local variable: {node.name}")
            locals_map[node.name] = self.alloc_word(0)
            if node.expr:
                self.collect_strings(node.expr)
        elif isinstance(node, If):
            self.collect_strings(node.cond)
            self.collect_function_locals(node.then_b, locals_map)
            if node.else_b:
                self.collect_function_locals(node.else_b, locals_map)
        elif isinstance(node, While):
            self.collect_strings(node.cond)
            self.collect_function_locals(node.body, locals_map)
        else:
            self.collect_strings(node)

    def collect_strings(self, node: ASTNode) -> None:
        if isinstance(node, StringLiteral):
            self.string_address(node.value)
        elif isinstance(node, Program):
            for decl in node.decls:
                self.collect_strings(decl)
        elif isinstance(node, Function):
            self.collect_strings(node.body)
        elif isinstance(node, Block):
            for stmt in node.stmts:
                self.collect_strings(stmt)
        elif isinstance(node, Let | Assign | Return | Unary):
            expr = getattr(node, "expr", None)
            if expr:
                self.collect_strings(expr)
        elif isinstance(node, If):
            self.collect_strings(node.cond)
            self.collect_strings(node.then_b)
            if node.else_b:
                self.collect_strings(node.else_b)
        elif isinstance(node, While):
            self.collect_strings(node.cond)
            self.collect_strings(node.body)
        elif isinstance(node, Call):
            for arg in node.args:
                self.collect_strings(arg)
        elif isinstance(node, Binary):
            self.collect_strings(node.left)
            self.collect_strings(node.right)

    def alloc_word(self, value: int) -> int:
        addr = self.next_data_addr
        self.data_image[addr] = value
        self.next_data_addr += 1
        return addr

    def string_address(self, value: str) -> int:
        if value in self.strings:
            return self.strings[value]
        start = self.next_data_addr
        for char in value:
            self.alloc_word(ord(char))
        self.alloc_word(0)
        self.strings[value] = start
        return start

    def generate_global_initializers(self) -> None:
        for decl in self.ast.program.decls:
            if isinstance(decl, Let) and decl.expr is not None:
                self.generate_expr(decl.expr)
                self.emit(Opcode.STORE, self.globals[decl.name])

    def generate_function(self, func: Function) -> None:
        if func.name in self.functions:
            raise CompileError(f"Duplicate function: {func.name}")
        self.functions[func.name] = self.current_pc()
        self.current_layout = self.layouts[func.name]
        self.generate_stmt(func.body)
        self.emit(Opcode.LIT, 0)
        self.emit(Opcode.RET)
        self.current_layout = None

    def generate_stmt(self, node: ASTNode) -> None:
        if isinstance(node, Block):
            for stmt in node.stmts:
                self.generate_stmt(stmt)
        elif isinstance(node, Let):
            self.generate_expr(node.expr) if node.expr else self.emit(Opcode.LIT, 0)
            self.emit(Opcode.STORE, self.var_address(node.name))
        elif isinstance(node, Assign):
            self.generate_expr(node.expr)
            self.emit(Opcode.DUP)
            self.emit(Opcode.STORE, self.var_address(node.name))
            self.emit(Opcode.DROP)
        elif isinstance(node, If):
            self.generate_condition_jump(node.cond, jump_if_false=True)
            false_jump = len(self.code) - 1
            self.generate_stmt(node.then_b)
            if node.else_b:
                end_jump = self.emit(Opcode.JMP, 0)
                self.patch_instruction(false_jump, self.current_pc())
                self.generate_stmt(node.else_b)
                self.patch_instruction(end_jump, self.current_pc())
            else:
                self.patch_instruction(false_jump, self.current_pc())
        elif isinstance(node, While):
            start = self.current_pc()
            self.generate_condition_jump(node.cond, jump_if_false=True)
            exit_jump = len(self.code) - 1
            self.generate_stmt(node.body)
            self.emit(Opcode.JMP, start)
            self.patch_instruction(exit_jump, self.current_pc())
        elif isinstance(node, Return):
            self.generate_expr(node.expr) if node.expr else self.emit(Opcode.LIT, 0)
            self.emit(Opcode.RET)
        elif isinstance(node, Call):
            self.generate_call(node)
            self.emit(Opcode.DROP)
        else:
            self.generate_expr(node)
            self.emit(Opcode.DROP)

    def generate_expr(self, node: ASTNode) -> None:
        if isinstance(node, IntLiteral):
            self.emit(Opcode.LIT, node.value)
        elif isinstance(node, StringLiteral):
            self.emit(Opcode.LIT, self.string_address(node.value))
        elif isinstance(node, Var):
            self.emit(Opcode.LOAD, self.var_address(node.name))
        elif isinstance(node, Assign):
            self.generate_expr(node.expr)
            self.emit(Opcode.DUP)
            self.emit(Opcode.STORE, self.var_address(node.name))
        elif isinstance(node, Unary):
            if node.op != "-":
                raise CompileError(f"Unknown unary operator: {node.op}")
            self.emit(Opcode.LIT, 0)
            self.generate_expr(node.expr)
            self.emit(Opcode.SUB)
        elif isinstance(node, Binary):
            self.generate_binary_expr(node)
        elif isinstance(node, Call):
            self.generate_call(node)
        else:
            raise CompileError(f"Unsupported expression: {type(node).__name__}")

    def generate_binary_expr(self, node: Binary) -> None:
        if node.op in {"+", "-", "*", "/", "%"}:
            self.generate_expr(node.left)
            self.generate_expr(node.right)
            ops = {"+": Opcode.ADD, "-": Opcode.SUB, "*": Opcode.MUL, "/": Opcode.DIV, "%": Opcode.MOD}
            self.emit(ops[node.op])
            return
        self.generate_condition_value(node)

    def generate_condition_value(self, node: Binary) -> None:
        false_jump_placeholder = self.generate_condition_jump(node, jump_if_false=True)
        self.emit(Opcode.LIT, 1)
        end_jump = self.emit(Opcode.JMP, 0)
        self.patch_instruction(false_jump_placeholder, self.current_pc())
        self.emit(Opcode.LIT, 0)
        self.patch_instruction(end_jump, self.current_pc())

    def generate_condition_jump(self, node: ASTNode, jump_if_false: bool) -> int:
        if not isinstance(node, Binary) or node.op not in {"==", "!=", "<", "<=", ">", ">="}:
            self.generate_expr(node)
            return self.emit(Opcode.IF if jump_if_false else Opcode.NIF, 0)

        if node.op == "==":
            self.generate_expr(node.left)
            self.generate_expr(node.right)
            self.emit(Opcode.SUB)
            return self.emit(Opcode.NIF if jump_if_false else Opcode.IF, 0)
        if node.op == "!=":
            self.generate_expr(node.left)
            self.generate_expr(node.right)
            self.emit(Opcode.SUB)
            return self.emit(Opcode.IF if jump_if_false else Opcode.NIF, 0)
        if node.op == "<":
            self.generate_expr(node.left)
            self.generate_expr(node.right)
            self.emit(Opcode.SUB)
            return self.emit(Opcode.MIF if jump_if_false else Opcode.IF, 0)
        if node.op == ">":
            self.generate_expr(node.right)
            self.generate_expr(node.left)
            self.emit(Opcode.SUB)
            return self.emit(Opcode.MIF if jump_if_false else Opcode.IF, 0)
        if node.op == "<=":
            self.generate_expr(node.right)
            self.generate_expr(node.left)
            self.emit(Opcode.SUB)
            return self.emit_synthesized_signed_jump(jump_on_nonnegative=not jump_if_false)
        self.generate_expr(node.left)
        self.generate_expr(node.right)
        self.emit(Opcode.SUB)
        return self.emit_synthesized_signed_jump(jump_on_nonnegative=not jump_if_false)

    def emit_synthesized_signed_jump(self, jump_on_nonnegative: bool) -> int:
        if jump_on_nonnegative:
            return self.emit(Opcode.MIF, 0)
        true_jump = self.emit(Opcode.MIF, 0)
        false_jump = self.emit(Opcode.JMP, 0)
        self.patch_instruction(true_jump, self.current_pc())
        return false_jump

    def generate_call(self, node: Call) -> None:
        if node.name == "getc":
            self.require_arity(node, 0)
            self.emit(Opcode.LOAD, INPUT_PORT_ADDR)
            return
        if node.name == "putc":
            self.require_arity(node, 1)
            self.generate_expr(node.args[0])
            self.emit(Opcode.STORE, OUTPUT_PORT_ADDR)
            self.emit(Opcode.LIT, 0)
            return
        if node.name in {"print", "puts"}:
            self.require_arity(node, 1)
            self.generate_print(node.args[0])
            self.emit(Opcode.LIT, 0)
            return
        if node.name == "ei":
            self.require_arity(node, 0)
            self.emit(Opcode.EI)
            self.emit(Opcode.LIT, 0)
            return
        if node.name == "di":
            self.require_arity(node, 0)
            self.emit(Opcode.DI)
            self.emit(Opcode.LIT, 0)
            return
        if node.name == "iret":
            self.require_arity(node, 0)
            self.emit(Opcode.IRET)
            self.emit(Opcode.LIT, 0)
            return
        self.generate_call_by_name(node.name, node.args)

    def generate_call_by_name(self, name: str, args: list[ASTNode]) -> None:
        if name not in self.layouts:
            raise CompileError(f"Undefined function: {name}")
        layout = self.layouts[name]
        if len(args) != len(layout.params):
            raise CompileError(f"{name} expects {len(layout.params)} argument(s), got {len(args)}")
        for arg, param in zip(args, layout.params):
            self.generate_expr(arg)
            self.emit(Opcode.STORE, layout.locals[param])
        index = self.emit(Opcode.CALL, 0)
        self.unresolved_calls.append((index, name))

    def generate_print(self, arg: ASTNode) -> None:
        if not isinstance(arg, StringLiteral):
            self.generate_expr(arg)
            self.emit(Opcode.STORE, OUTPUT_PORT_ADDR)
            return
        self.generate_expr(arg)
        self.emit(Opcode.TOA)
        begin = self.current_pc()
        self.emit(Opcode.ALOADP)
        self.emit(Opcode.DUP)
        exit_jump = self.emit(Opcode.IF, 0)
        self.emit(Opcode.STORE, OUTPUT_PORT_ADDR)
        self.emit(Opcode.JMP, begin)
        self.patch_instruction(exit_jump, self.current_pc())
        self.emit(Opcode.DROP)

    def require_arity(self, node: Call, arity: int) -> None:
        if len(node.args) != arity:
            raise CompileError(f"{node.name} expects {arity} argument(s), got {len(node.args)}")

    def var_address(self, name: str) -> int:
        if self.current_layout and name in self.current_layout.locals:
            return self.current_layout.locals[name]
        if name in self.globals:
            return self.globals[name]
        raise CompileError(f"Undefined variable: {name}")

    def patch_calls(self) -> None:
        for index, name in self.unresolved_calls:
            self.patch_instruction(index, self.functions[name])


class MachineCode:
    def __init__(self, ast: AbstractSyntaxTree):
        self.codegen = CodeGen(ast)
        self.codegen.generate()
        self.code = self.codegen.code
        self.data_image = self.codegen.data_image

    def store(self, filename: str) -> None:
        target = Path(filename)
        target.write_bytes(to_bytes(self.code))
        target.with_suffix(target.suffix + ".lst").write_text(
            listing(self.code, self.data_image),
            encoding="utf-8",
        )
        config = {
            "input_port": INPUT_PORT_ADDR,
            "output_port": OUTPUT_PORT_ADDR,
            "data_memory_size": 32000,
            "command_memory_size": 32000,
            "entry_point": 0,
            "data_image": self.data_image,
        }
        target.with_suffix(target.suffix + ".config.json").write_text(
            json.dumps(config, indent=4),
            encoding="utf-8",
        )


def compile_file(input_file: str, output_file: str) -> MachineCode:
    ast = AbstractSyntaxTree(input_file)
    Path(input_file + ".ast").write_text(ast.get_ast_dump(), encoding="utf-8")
    machine_code = MachineCode(ast)
    machine_code.store(output_file)
    return machine_code


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sleekstack-translator",
        description="Compile SleekStack source into binary stack-machine code.",
    )
    parser.add_argument("input_file")
    parser.add_argument("output_machine_code_file")
    args = parser.parse_args()
    try:
        compile_file(args.input_file, args.output_machine_code_file)
    except CompileError as error:
        parser.exit(1, f"translator error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
