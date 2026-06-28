from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from storage.datatypes import (
    DataType, 
    DataTypeError,
    NULL_FLAG_NULL,
    NULL_FLAG_PRESENT,
    NULL_FLAG_SIZE,
    resolve_type
)

from storage.page import HEADER_SIZE, PAGE_SIZE, CorruptPageError, Page, PageFullError

class TableFileError(Exception):
    """Generic error for table-file level problems."""

class RowNotFoundError(Exception):
    """Raised when a RowID does not point to a live record."""

@dataclass(frozen=True)
class Column:
    name: str
    data_type: DataType
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False

    @property
    def slot_size(self) -> int:
        return NULL_FLAG_SIZE + self.data_type.byte_size()


@dataclass(frozen=True)
class RowID:
    page_number: int
    slot_number: int

    def __repr__(self) -> str:
        return f"RowID(page={self.page_number}, slot={self.slot_number})"
    

class Schema:
    def __init__(self, columns: list[Column]):
        if not columns:
            raise TableFileError("A table must have at least one column")
        
        names_seen = set()
        for col in columns:
            if col.name in names_seen:
                raise TableFileError(f"Duplicate column name: {col.name!r}")
            names_seen.add(col.name)
        
        self.columns = columns

        offsets = []
        running_offset = 0
        for col in columns:
            offsets.append(running_offset)
            running_offset += col.slot_size
        self._offsets = offsets
        self.record_size = running_offset

    def index_of(self, column_name: str) -> int:
        for i, col in enumerate(self.columns):
            if col.name == column_name:
                return i
        raise TableFileError(f"No such column: {column_name!r}")
    
    def offset_of(self, column_index: int) -> int:
        return self._offsets[column_index]
    
    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]
    
    def __len__(self) -> int:
        return len(self.columns)

    def __iter__(self):
        return iter(self.columns)
    

def encode_row(schema: Schema, values: dict[str, Any]) -> bytes:
    parts = []
    for col in schema.columns:
        value = values.get(col.name)

        if value is None:
            if not col.nullable:
                raise DataTypeError(f"Column {col.name!r} is NOT NULL but got NULL")
            
            parts.append(NULL_FLAG_NULL)
            parts.append(b"\x00" * col.data_type.byte_size())
            continue
        
        col.data_type.validate(value)
        parts.append(NULL_FLAG_PRESENT)
        parts.append(col.data_type.encode(value))

    record = b"".join(parts)
    assert len(record) == schema.record_size, (
        f"encode_row produced {len(record)} bytes, expected {schema.record_size} "
        f"- this indicates a bug in Schema/DataType bytes_size accounting"
    )
    return record

def decode_row(schema: Schema, raw: bytes) -> dict[str, Any]:
    if len(raw) != schema.record_size:
        raise CorruptPageError(
            f"record is {len(raw)} bytes, schema expects {schema.record_size}"
        )
    
    row: dict[str, Any] = {}
    cursor = 0
    for col in schema.columns:
        null_flag = raw[cursor:cursor + NULL_FLAG_SIZE]
        cursor += NULL_FLAG_SIZE

        value_size = col.data_type.byte_size()
        value_bytes = raw[cursor: cursor + value_size]
        cursor += value_size

        if null_flag == NULL_FLAG_NULL:
            row[col.name] = None
        else:
            row[col.name] = col.data_type.decode(value_bytes)
    return row


class TableFile:
    def __init__(self, file_path: str, schema: Schema):
        self.file_path = file_path
        self.schema = schema

        if not os.path.exists(file_path):
            open(file_path, "wb").close()

    def _num_pages(self) -> int:
        size = os.path.getsize(self.file_path)
        if size % PAGE_SIZE != 0:
            raise CorruptPageError(
                f"{self.file_path} size {size} is not a multiple of PAGE_SIZE "
                f"({PAGE_SIZE}) - file may be corrupted or truncated"
            )
        return size // PAGE_SIZE
    
    def _read_page(self, page_number: int) -> Page:
        with open(self.file_path, "rb") as f:
            f.seek(page_number * PAGE_SIZE)
            raw = f.read(PAGE_SIZE)
        if len(raw) != PAGE_SIZE:
            raise CorruptPageError(
                f"Expected to read {PAGE_SIZE} bytes for page {page_number},  "
                f"got {len(raw)} - file may be truncated"
            )
        return Page.from_bytes(raw, record_size=self.schema.record_size)
    
    def _write_page(self, page_number: int, page: Page) -> None:
        with open(self.file_path, "r+b") as f:
            f.seek(page_number * PAGE_SIZE)
            f.write(page.to_bytes())

    def _append_new_page(self, page: Page) -> int:
        page_number = self._num_pages()
        with open(self.file_path, "ab") as f:
            f.write(page.to_bytes())
        return page_number
    
    def insert(self, values: dict[str, Any]) -> RowID:
        record_bytes = encode_row(self.schema, values)
        num_pages = self._num_pages()

        for page_number in range(num_pages):
            page = self._read_page(page_number)
            if page.has_free_slot():
                slot_number = page.insert(record_bytes)
                self._write_page(page_number, page)
                return RowID(page_number, slot_number)
            
        new_page = Page(record_size=self.schema.record_size)
        slot_number = new_page.insert(record_bytes)
        page_number = self._append_new_page(new_page)
        return RowID(page_number, slot_number)
    
    def get(self, row_id: RowID) -> dict[str, Any]:
        page = self._read_page(row_id.page_number)
        raw = page.get(row_id.slot_number)
        if raw is None:
            raise RowNotFoundError(f"No row at {row_id}")
        return decode_row(self.schema, raw)
    
    def update(self, row_id: RowID, values: dict[str, Any]) -> None:
        record_bytes = encode_row(self.schema, values)
        page = self._read_page(row_id.page_number)
        if page.get(row_id.slot_number) is None:
            raise RowNotFoundError(f"No row at {row_id}")
        page.update(row_id.slot_number, record_bytes)
        self._write_page(row_id.page_number, page)

    def delete(self, row_id: RowID) -> None:
        page = self._read_page(row_id.page_number)
        if page.get(row_id.slot_number) is None:
            raise RowNotFoundError(f"No row at {row_id}")
        page.delete(row_id.slot_number)
        self._write_page(row_id.page_number, page)

    def scan(self) -> Iterator[tuple[RowID, dict[str, Any]]]:
        num_pages = self._num_pages()
        for page_number in range(num_pages):
            page = self._read_page(page_number)
            for slot_number, raw in page.iter_occupied():
                row_id = RowID(page_number, slot_number)
                yield row_id, decode_row(self.schema, raw)

    def count_rows(self) -> int:
        return sum(1 for _ in self.scan())
    
    def truncate(self) -> None:
        open(self.file_path, "wb").close()

    def __repr__(self) -> str:
        return (
            f"TableFile(path={self.file_path!r}, "
            f"record_size={self.schema.record_size}, "
            f"pages={self._num_pages()})"
        )