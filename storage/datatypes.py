from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

class DataTypeError(Exception):
    """"""

class DataType(ABC):
    sql_names: frozenset[str] = frozenset()

    @abstractmethod
    def byte_size(self) -> int:
        raise NotImplementedError
    
    @abstractmethod
    def encode(self, value: Any) -> bytes:
        raise NotImplementedError
    
    @abstractmethod
    def decode(self, raw: bytes) -> Any:
        raise NotImplementedError
    
    @abstractmethod
    def validate(self, value: Any) -> None:
        raise NotImplementedError
    
    def __repr__(self) -> str:
        return self.__class__.__name__
    

NULL_FLAG_SIZE = 1
NULL_FLAG_NULL = b"\x01"
NULL_FLAG_PRESENT = b"\x00"

class IntType(DataType):
    sql_names = frozenset({"INT", "INTEGER"})
    _STRUCT_FORMAT = ">i"

    def byte_size(self) -> int:
        return struct.calcsize(self._STRUCT_FORMAT)
    
    def validate(self, value: Any) -> None:
        if value is None:
            return 
        if isinstance(value, bool):
            raise DataTypeError(f"expected INT, got BOOL: {value!r}")
        if not isinstance(value, int):
            raise DataTypeError(f"expected INT, got {type(value).__name__}: {value!r}")
        if not (-2_147_483_648 <= value <= 2_147_483_647):
            raise DataTypeError(f"INT value out of range: {value!r}")
        
    def encode(self, value: Any) -> bytes:
        return struct.pack(self._STRUCT_FORMAT, value)
    
    def decode(self, raw: bytes) -> int:
        return struct.unpack(self._STRUCT_FORMAT, raw)[0]
    
class FloatType(DataType):
    sql_names = frozenset({"FLOAT", "DOUBLE"})
    _STRUCT_FORMAT = ">d"

    def byte_size(self) -> int:
        return struct.calcsize(self._STRUCT_FORMAT)
    
    def validate(self, value: Any) -> None:
        if value is None:
            return 
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DataTypeError(f"expected FLOAT, got {type(value).__name__}: {value!r}")
        
    def encode(self, value: Any) -> bytes:
        return struct.pack(self._STRUCT_FORMAT, float(value))
    
    def decode(self, raw: bytes) -> float:
        return struct.unpack(self._STRUCT_FORMAT, raw)[0]
    
class BoolType(DataType):
    sql_names = frozenset({"BOOL", "BOOLEAN"})
    _STRUCT_FORMAT = ">B"

    def byte_size(self) -> int:
        return struct.calcsize(self._STRUCT_FORMAT)
    
    def validate(self, value: Any) -> None:
        if value is None:
            return 
        if not isinstance(value, bool):
            raise DataTypeError(f"expected BOOL, got {type(value).__name__}: {value!r}")

    def encode(self, value: Any) -> bytes:
        return struct.pack(self._STRUCT_FORMAT, 1 if value else 0)

    def decode(self, raw: bytes) -> bool:
        return struct.unpack(self._STRUCT_FORMAT, raw)[0] == 1
    
class StringType(DataType):
    sql_names = frozenset({"STRING", "VARCHAR", "CHAR", "TEXT"})

    DEFAULT_MAX_LENGTH = 255

    def __init__(self, max_length: int = DEFAULT_MAX_LENGTH):
        if max_length <= 0:
            raise ValueError("max_length must be positive")
        self.max_length = max_length

    def byte_size(self) -> int:
        return self.max_length
    
    def validate(self, value: Any) -> None:
        if value is None:
            return 
        if not isinstance(value, str):
            raise DataTypeError(f"expected STRING, got {type(value).__name__}: {value!r}")
        
        encoded_len = len(value.encode("utf-8"))
        if encoded_len > self.max_length:
            raise DataTypeError(
                f"string {value!r} is {encoded_len} bytes (utf-8), "
                f"exceeds column max_length={self.max_length}"
            )
        
    def encode(self, value: Any) -> bytes:
        raw = value.encode("utf-8")
        return raw + b"\x00" * (self.max_length - len(raw))
    
    def decode(self, raw: bytes) -> str:
        return raw.rstrip(b"\x00").decode("utf-8")
    
    def __repr__(self) -> str:
        return f"StringType(max_length={self.max_length})"
    
class DateType(DataType):
    sql_names = frozenset({"DATE"})
    _STRUCT_FORMAT = ">i"
    _EPOCH = date(1970, 1, 1)
 
    def byte_size(self) -> int:
        return struct.calcsize(self._STRUCT_FORMAT)
 
    def _parse(self, value: Any) -> date:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise DataTypeError(
                    f"DATE value {value!r} must be 'YYYY-MM-DD'"
                ) from exc
        raise DataTypeError(f"expected DATE, got {type(value).__name__}: {value!r}")
 
    def validate(self, value: Any) -> None:
        if value is None:
            return
        self._parse(value)
 
    def encode(self, value: Any) -> bytes:
        d = self._parse(value)
        days = (d - self._EPOCH).days
        return struct.pack(self._STRUCT_FORMAT, days)
 
    def decode(self, raw: bytes) -> str:
        days = struct.unpack(self._STRUCT_FORMAT, raw)[0]
        d = date.fromordinal(self._EPOCH.toordinal() + days)
        return d.isoformat()
    

_BASE_TYPES = [IntType(), FloatType(), BoolType(), DateType()]
 
 
def resolve_type(sql_type_name: str, length: int | None = None) -> DataType:
    name = sql_type_name.strip().upper()
 
    if name in StringType.sql_names:
        return StringType(max_length=length or StringType.DEFAULT_MAX_LENGTH)
 
    for base_type in _BASE_TYPES:
        if name in base_type.sql_names:
            return base_type
 
    raise DataTypeError(f"Unknown SQL data type: {sql_type_name!r}")

    