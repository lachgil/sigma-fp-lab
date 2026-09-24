"""Freestanding Clang/LLD C backend for build_module.py, not an ELF linker.

LLD resolves code references and emits dynamic R_ARM_RELATIVE relocations.
We accept only that checked relocation subset, materialize zero data, and let
module_c_bootstrap.S relocate data once at the module's actual heap address.
Intermediate objects and ELF files are temporary and never deployed.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Section:
    name: str
    kind: int
    flags: int
    address: int
    offset: int
    size: int
    link: int
    alignment: int
    entry_size: int


class Elf:
    """Read the small ELF32 surface needed to audit compiler/linker output."""

    def __init__(self, path: Path, expected_type: int):
        self.data = path.read_bytes()
        if len(self.data) < 52 or self.data[:7] != b'\x7fELF\x01\x01\x01':
            raise ValueError('C toolchain must produce little-endian ELF32')
        header = struct.unpack_from('<16sHHIIIIIHHHHHH', self.data)
        if header[1:4] != (expected_type, 40, 1):
            raise ValueError('C toolchain produced an incompatible ARM ELF')
        if header[7] & 0x400:
            raise ValueError('hard-float ARM ABI is unsupported; use soft-float')
        shoff, shsize, shcount, names_index = header[6], header[11], header[12], header[13]
        if shsize != 40 or not shcount or not 0 < names_index < shcount:
            raise ValueError('unsupported ELF section table')
        self._bytes(shoff, shsize * shcount)
        headers = [struct.unpack_from('<10I', self.data, shoff + i * shsize)
                   for i in range(shcount)]
        names = headers[names_index]
        strings = self._bytes(names[4], names[5])
        self.sections = []
        for name, kind, flags, address, offset, size, link, _, alignment, entry_size in headers:
            if kind != 8:  # SHT_NOBITS has memory size but no file bytes.
                self._bytes(offset, size)
            self.sections.append(Section(self._string(strings, name), kind, flags,
                                         address, offset, size, link, alignment, entry_size))

    def _bytes(self, offset: int, size: int) -> bytes:
        if offset > len(self.data) or size > len(self.data) - offset:
            raise ValueError('truncated ELF output from C toolchain')
        return self.data[offset:offset + size]

    @staticmethod
    def _string(strings: bytes, offset: int) -> str:
        if offset >= len(strings) or b'\0' not in strings[offset:]:
            raise ValueError('invalid ELF string table')
        return strings[offset:strings.index(b'\0', offset)].decode('utf-8')

    def symbols(self):
        for section in self.sections:
            if section.kind not in (2, 11):  # SYMTAB, DYNSYM
                continue
            if (section.entry_size != 16 or section.size % 16
                    or not 0 < section.link < len(self.sections)):
                raise ValueError('invalid ELF symbol table')
            names = self.sections[section.link]
            strings = self._bytes(names.offset, names.size)
            for offset in range(section.offset, section.offset + section.size, 16):
                name, value, size, info, _, index = struct.unpack_from('<IIIBBH', self.data, offset)
                yield self._string(strings, name), value, size, info & 15, index

    def reject_imports(self, *, object_file: bool = False) -> None:
        for name, _, _, kind, index in self.symbols():
            if kind in (6, 10):
                raise ValueError(f'unsupported TLS/indirect-function symbol: {name}')
            if name and index == 0:
                if object_file and name == '_GLOBAL_OFFSET_TABLE_':
                    continue  # Conventional linker-defined PIC anchor, not an import.
                raise ValueError(f'unresolved C import: {name}; provide its definition in '
                                 'the source (no libc or compiler support library is linked)')


def _tool(requested: str | Path | None, default: str, environment: str) -> str:
    selected = str(requested or os.environ.get(environment) or default)
    found = shutil.which(selected)
    if found:
        return found
    if selected == 'ld.lld':
        local = ROOT / 'builds/toolchain/bin/ld.lld'
        if local.is_file() and os.access(local, os.X_OK):
            return str(local)
    raise ValueError(f'C modules require {selected}; install clang and lld '
                     '(Arch: pacman -S clang lld; Debian/Ubuntu: apt install clang lld), '
                     f'or set {environment}/the corresponding tool option')


def _run(command: list[str], stage: str) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        diagnostic = result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'
        raise ValueError(f'C {stage} failed:\n{shlex.join(command)}\n{diagnostic}')


def _image(elf: Elf, module_id: int, abi: dict) -> bytes:
    elf.reject_imports()
    allowed = {'.fp.header', '.text', '.rodata', '.data', '.got', '.dynamic',
               '.dynsym', '.dynstr', '.hash', '.gnu.hash', '.rel.dyn', '.bss', '.fp.state'}
    allocated = sorted((s for s in elf.sections if s.flags & 2 and s.size),
                       key=lambda s: s.address)
    previous_end = 0
    for section in allocated:
        if section.alignment > 8:
            raise ValueError(f'unsupported alignment in {section.name}: '
                             'module allocation guarantees only 8-byte alignment')
        if section.name not in allowed or section.flags & 0x400:
            raise ValueError(f'unsupported allocated C section: {section.name}; '
                             'TLS, constructors, destructors and custom sections are unsupported')
        if section.address < previous_end:
            raise ValueError('overlapping ELF image sections')
        previous_end = section.address + section.size
    symbols = {name: (value, kind, index) for name, value, _, kind, index in elf.symbols()}
    required = ('__fp_c_image_bytes', '__fp_c_initialize', '__fp_c_invoke',
                '__fp_c_state_offset', '__fp_c_rel_start_offset', '__fp_c_rel_end_offset',
                'fp_module_init', 'fp_module_invoke')
    for name in required:
        if name not in symbols:
            raise ValueError(f'missing C module symbol: {name}')
    size = symbols['__fp_c_image_bytes'][0]
    if not abi['MOD_HEADER_BYTES'] < size < 0x1000000 or size % 4 or size != previous_end:
        raise ValueError('invalid C image extent (maximum supported size is 16 MiB)')
    for name in ('__fp_c_initialize', '__fp_c_invoke', 'fp_module_init', 'fp_module_invoke'):
        value, kind, index = symbols[name]
        if (kind != 2 or not 0 < index < len(elf.sections)
                or not elf.sections[index].flags & 4 or value % 4
                or not abi['MOD_HEADER_BYTES'] <= value < size):
            raise ValueError(f'{name} must be a defined, aligned ARM function')
    blob = bytearray(size)  # NOBITS, section gaps, and tail padding are explicit zero bytes.
    for section in allocated:
        if section.kind != 8:
            blob[section.address:section.address + section.size] = elf._bytes(section.offset, section.size)
    if symbols['__fp_c_initialize'][0] != abi['MOD_HEADER_BYTES']:
        raise ValueError('C bootstrap is not immediately after the ABI1 header')
    state = symbols['__fp_c_state_offset'][0]
    if not any(s.name == '.fp.state' and s.address == state and s.size == 4 for s in allocated):
        raise ValueError('invalid C relocation guard')
    rel_start = symbols['__fp_c_rel_start_offset'][0]
    rel_end = symbols['__fp_c_rel_end_offset'][0]
    rel_sections = [s for s in elf.sections if s.kind in (4, 9) and s.size]
    if rel_end - rel_start != sum(s.size for s in rel_sections):
        raise ValueError('C bootstrap does not cover every ELF relocation')
    targets = set()
    for section in rel_sections:
        if (section.name != '.rel.dyn' or section.kind != 9 or section.entry_size != 8
                or section.size % 8 or section.address != rel_start
                or section.address + section.size != rel_end):
            raise ValueError(f'unsupported ELF relocation section: {section.name}')
        for offset in range(section.offset, section.offset + section.size, 8):
            target, info = struct.unpack_from('<II', elf.data, offset)
            if info != 23:  # R_ARM_RELATIVE with symbol index zero; no other dynamic fixups.
                raise ValueError(f'unsupported ARM relocation type {info & 255} '
                                 f'(symbol {info >> 8}) at image offset {target:#x}')
            if (target % 4 or target in targets
                    or not any(s.name in ('.data', '.got') and s.flags & 1
                               and s.address <= target <= s.address + s.size - 4 for s in allocated)):
                raise ValueError(f'unsupported C relocation target {target:#x}; '
                                 'only aligned writable data/GOT words may be relocated')
            addend = struct.unpack_from('<I', blob, target)[0]
            if addend > size:
                raise ValueError(f'C relocation at {target:#x} points outside the module image')
            targets.add(target)
    struct.pack_into('<6I', blob, 0, abi['FP_MODULE_MAGIC'], abi['FP_ABI'], module_id, size,
                     symbols['__fp_c_initialize'][0], symbols['__fp_c_invoke'][0])
    return bytes(blob)


def compile_c_module(source: Path, module_id: int, abi: dict, defines=(), *,
                     compiler: str | Path | None = None,
                     linker: str | Path | None = None) -> bytes:
    """Build one freestanding C translation unit; return an ABI1 module or raise.

    No source, output, card, or runtime is modified. All intermediate artifacts
    disappear on success or rejection. User-supplied libc/AEABI implementations
    may be included in this translation unit; imports are never stubbed.
    """
    if not 0 < module_id <= 0xffffffff:
        raise ValueError('C module ID must be a nonzero uint32')
    clang = _tool(compiler, 'clang', 'FP_MODULE_CLANG')
    lld = _tool(linker, 'ld.lld', 'FP_MODULE_LLD')
    with tempfile.TemporaryDirectory(prefix='fp-module-c-') as directory:
        work = Path(directory)
        contract = work / 'callbacks.h'
        contract.write_text('#include "module_abi.h"\n'
                            'int32_t fp_module_init(const struct fp_api *, const struct fp_record *);\n'
                            'uint32_t fp_module_invoke(const struct fp_api *, '
                            'const struct fp_record *, uint32_t);\n')
        target = [clang, '--target=armv7-none-eabi', '-marm', '-mfloat-abi=soft']
        source_object = work / 'module.o'
        _run(target + ['-std=c11', '-Os', '-ffreestanding', '-fno-builtin', '-fPIC',
                       '-fvisibility=hidden', '-fno-stack-protector', '-fno-common',
                       '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                       '-mno-unaligned-access', '-Wall', '-Wextra',
                       '-Werror=implicit-function-declaration', '-I', str(ROOT / 'src'),
                       '-include', str(contract)] + [f'-D{define}' for define in defines]
             + ['-c', str(Path(source).resolve()), '-o', str(source_object)], 'compilation')
        Elf(source_object, 1).reject_imports(object_file=True)
        bootstrap_object = work / 'bootstrap.o'
        _run(target + ['-c', str(TOOLS / 'module_c_bootstrap.S'), '-o', str(bootstrap_object)],
             'bootstrap assembly')
        linked = work / 'module.elf'
        _run([lld, '-shared', '--no-undefined', '-Bsymbolic', '-z', 'text', '--fatal-warnings',
              '--build-id=none', '--hash-style=sysv', '-T', str(TOOLS / 'module_c.ld'),
              str(bootstrap_object), str(source_object), '-o', str(linked)], 'link')
        return _image(Elf(linked, 3), module_id, abi)
