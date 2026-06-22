from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

PAGE_SIZE = 4096

HEADER_FORMAT = ">ii"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

class PageFullError(Exception):
    """"""
class CorruptPageError(Exception):
    """"""
@dataclass
class PageHeader:
    num_slots_used: int
    capacity: int

class Page:
    def __init__(self, record_size: int, capacity: Optional[int] = None):
        self.record_size = record_size
        if capacity is None:
            capacity = self._max_capacity_for(record_size)
        self.capacity = capacity

        self.bitmap_size = (self.capacity + 7) // 8

        self.occupied: list[bool] = [False] * self.capacity

        self.slots: list[bytes] = [b"\x00" * record_size for _ in range(self.capacity)]
        self.num_slots_used = 0

    @classmethod
    def _max_capacity_for(cls, record_size: int) -> int:
        usable = PAGE_SIZE - HEADER_SIZE
        capacity = 0
        while True:
            bitmap_size = (capacity + 8) // 8
            if HEADER_SIZE + bitmap_size + (capacity + 1) * record_size > PAGE_SIZE:
                break
            capacity += 1
        
        if capacity == 0:
            raise ValueError(
                f"record_size={record_size} is too large to fit even one "
                f"record in a {PAGE_SIZE}-byte page."
            )

        return capacity

    def insert(self, record_bytes: bytes) -> int:
        if len(record_bytes) != self.record_size:
            raise ValueError(
                f"record is {len(record_bytes)} bytes, "
                f"expected exactly {self.record_size}"
            )
        
        for slot_no in range(self.capacity):
            if not self.occupied[slot_no]:
                self.slots[slot_no] = record_bytes
                self.occupied[slot_no] = True
                self.num_slots_used += 1
                return slot_no
            
        raise PageFullError("No free slot in this page")

    def get(self, slot_no: int) -> Optional[bytes]:
        self._check_slot_no(slot_no)
        if not self.occupied[slot_no]:
            return None
        return self.slots[slot_no]

    def delete(self, slot_no: int) -> None:
        self._check_slot_no(slot_no)
        if self.occupied[slot_no]:
            self.occupied[slot_no] = False
            self.slots[slot_no] = b"\x00" * self.record_size
            self.num_slots_used -= 1

    def update(self, slot_no: int, record_bytes: bytes) -> None:
        self._check_slot_no(slot_no)
        if len(record_bytes) != self.record_size:
            raise ValueError(
                f"record is {len(record_bytes)} bytes, "
                f"expected exactly {self.record_size}"
            )
        
        if not self.occupied[slot_no]:
            raise ValueError(f"slot {slot_no} is empty, cannot update")
        self.slots[slot_no] = record_bytes

    def has_free_slot(self) -> bool:
        return self.num_slots_used < self.capacity

    def iter_occupied(self):
        for slot_no in range(self.capacity):
            if self.occupied[slot_no]:
                yield slot_no, self.slots[slot_no]

    def _check_slot_no(self, slot_no: int) -> None:
        if not (0 <= slot_no < self.capacity):
            raise IndexError(f"slot_no {slot_no} out of range (capacity={self.capacity})")
        

    def to_bytes(self) -> bytes:
        header = struct.pack(HEADER_FORMAT, self.num_slots_used, self.capacity)
        bitmap = bytearray(self.bitmap_size)

        for slot_no, is_occupied in enumerate(self.occupied):
            if is_occupied:
                byte_index = slot_no // 8
                bit_index = slot_no % 8
                bitmap[byte_index] |= (1 << bit_index)
        
        body = b"".join(self.slots)
        page_bytes = header + bytes(bitmap) + body

        padding_needed = PAGE_SIZE - len(page_bytes)
        if padding_needed < 0:
            raise CorruptPageError(
                f"Page content ({len(page_bytes)} bytes) exceeds PAGE_SIZE "
                f"({PAGE_SIZE}). record_size={self.record_size}, "
                f"capacity={self.capacity} is misconfigured."
            )
        return page_bytes + b"\x00" * padding_needed

    @classmethod
    def from_bytes(cls, raw: bytes, record_size: int) -> "Page":
        if len(raw) != PAGE_SIZE:
            raise CorruptPageError(
                f"Expected exactly {PAGE_SIZE} bytes, got {len(raw)}"
            )
        
        num_slots_used, capacity = struct.unpack(HEADER_FORMAT, raw[:HEADER_SIZE])

        page = cls(record_size=record_size, capacity=capacity)

        bitmap_start = HEADER_SIZE
        bitmap_end = bitmap_start + page.bitmap_size
        bitmap = raw[bitmap_start:bitmap_end]

        body_start = bitmap_end
        occupied = []
        for slot_no in range(capacity):
            byte_index = slot_no // 8
            bit_index = slot_no % 8
            is_occupied = bool(bitmap[byte_index] & (1 << bit_index))
            occupied.append(is_occupied)
        
        slots = []
        for slot_no in range(capacity):
            start = body_start + slot_no * record_size
            end = start + record_size
            slots.append(raw[start:end])
        
        page.occupied = occupied
        page.slots = slots
        page.num_slots_used = num_slots_used

        return page

    def __repr__(self) -> str:
        return (
            f"Page(record_size={self.record_size}, capacity={self.capacity}, "
            f"used={self.num_slots_used}/{self.capacity})"
        )