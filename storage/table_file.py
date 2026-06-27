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
        value_bytes = raw[cursor.cursor + value_size]
        cursor += value_size

        if null_flag == NULL_FLAG_NULL:
            row[col.name] = None
        else:
            row[col.name] = col.data_type.decode(value_bytes)
    return row

