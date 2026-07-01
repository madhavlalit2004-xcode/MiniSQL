from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from typing import Any, Optional

from storage.datatypes import DataTypeError, StringType, resolve_type
from storage.table_file import Column, Schema, TableFile

class CatalogError(Exception):
    """"""

class DatabaseNotFoundError(CatalogError):
    pass

class DatabaseAlreadyExistsError(CatalogError):
    pass

class TableNotFoundError(CatalogError):
    pass

class TableAlreadyExistsError(CatalogError):
    pass


#JSON helpers - Schema
def _schema_to_dict(schema: Schema) -> list[dict]:
    columns = []
    for col in schema.columns:
        dtype = col.data_type
        col_dict: dict[str, Any] = {
            "name": col.name, 
            "nullable": col.nullable, 
            "primary_key": col.primary_key, 
            "unique": col.unique, 
        }

        if isinstance(dtype, StringType):
            col_dict["type"] = "String"
            col_dict["length"] = dtype.max_length
        else:
            col_dict["type"] = next(iter(dtype.sql_names))
            col_dict["length"] = None
        
        columns.append(col_dict)
    return columns


def _dict_to_schema(columns_data: list[dict]) -> Schema:
    columns = []
    for col_dict in columns_data:
        dtype = resolve_type(col_dict["type"], length=col_dict.get("length"))
        col = Column(
            name=col_dict["name"],
            data_type=dtype,
            nullable=col_dict.get("nullable", True),
            primary_key=col_dict.get("primary_key", False),
            unique=col_dict.get("unique", False),
        )
        columns.append(col)
    return Schema(columns)


#TABLEMETA: lightweight descriptor for a registered table
class TableMeta:
    def __init__(
        self,
        name: str,
        schema: Schema,
        tbl_path: str,
        created_at: str,
    ):
        self.name = name
        self.schema = schema
        self.tbl_path = tbl_path
        self.created_at = created_at
 
    def open_table(self) -> TableFile:
        return TableFile(self.tbl_path, self.schema)
 
    def __repr__(self) -> str:
        cols = ", ".join(
            f"{c.name}:{c.data_type}" for c in self.schema.columns
        )
        return f"TableMeta({self.name!r}, [{cols}])"
    

#CATALOG - the top level registry
class Catalog:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = os.path.abspath(data_dir)
        self._current_db: Optional[str] = None

        self._cache: dict[str, dict[str, TableMeta]] = {}
        self._bootstrap()


    #BOOTSTRAP - ensure the data directory and global schema file exist
    def _bootstrap(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        schema_path = self._info_schema_path()
        if not os.path.exists(schema_path):
            self._write_info_schema([])
 
    def _info_schema_path(self) -> str:
        return os.path.join(self.data_dir, "information_schema.json")
 
    def _db_dir(self, db_name: str) -> str:
        return os.path.join(self.data_dir, db_name)
 
    def _catalog_path(self, db_name: str) -> str:
        return os.path.join(self._db_dir(db_name), "catalog.json")
 
    def _table_dir(self, db_name: str, table_name: str) -> str:
        return os.path.join(self._db_dir(db_name), table_name)
 
    def _schema_path(self, db_name: str, table_name: str) -> str:
        return os.path.join(self._table_dir(db_name, table_name), "schema.json")
 
    def _tbl_path(self, db_name: str, table_name: str) -> str:
        return os.path.join(self._table_dir(db_name, table_name), "data.tbl")
    

    #information_schema.json I/O
    def _read_info_schema(self) -> list[dict]:
        with open(self._info_schema_path(), "r", encoding="utf-8") as f:
            return json.load(f)
 
    def _write_info_schema(self, databases: list[dict]) -> None:
        with open(self._info_schema_path(), "w", encoding="utf-8") as f:
            json.dump(databases, f, indent=2)


    # catalog.json I/O (per-database table registry)
    def _read_catalog(self, db_name: str) -> list[dict]:
        path = self._catalog_path(db_name)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
 
    def _write_catalog(self, db_name: str, tables: list[dict]) -> None:
        with open(self._catalog_path(db_name), "w", encoding="utf-8") as f:
            json.dump(tables, f, indent=2)


    # DATABASE OPERATIONS
    def create_database(self, db_name: str) -> None:
        db_name = db_name.lower()
        if self.database_exists(db_name):
            raise DatabaseAlreadyExistsError(f"Database {db_name!r} already exists")
 
        os.makedirs(self._db_dir(db_name), exist_ok=True)
        self._write_catalog(db_name, [])

        databases = self._read_info_schema()
        databases.append({
            "name": db_name,
            "created_at": datetime.utcnow().isoformat(),
        })
        self._write_info_schema(databases)
    
    def drop_database(self, db_name: str) -> None:
        db_name = db_name.lower()
        if not self.database_exists(db_name):
            raise DatabaseNotFoundError(f"Database {db_name!r} does not exist")
 
        shutil.rmtree(self._db_dir(db_name))
 
        databases = [
            d for d in self._read_info_schema() if d["name"] != db_name
        ]
        self._write_info_schema(databases)
        self._cache.pop(db_name, None)

        if self._current_db == db_name:
            self._current_db = None

    def use_database(self, db_name: str) -> None:
        db_name = db_name.lower()
        if not self.database_exists(db_name):
            raise DatabaseNotFoundError(f"Database {db_name!r} does not exist")
        self._current_db = db_name
        # Eagerly populate the cache for this database now
        self._load_db_cache(db_name)
 
    def database_exists(self, db_name: str) -> bool:
        return os.path.isdir(self._db_dir(db_name.lower()))
    
    def list_databases(self) -> list[dict]:
        return self._read_info_schema()
    
    @property
    def current_db(self) -> Optional[str]:
        return self._current_db
    
    def _require_current_db(self) -> str:
        if self._current_db is None:
            raise CatalogError(
                "No database selected. Run: USE <database_name>"
            )
        return self._current_db
    

    # IN MEMORY CACHE MANAGEMENT
    def _load_db_cache(self, db_name: str) -> None:
        if db_name in self._cache:
            return  # already loaded
 
        self._cache[db_name] = {}
        for entry in self._read_catalog(db_name):
            table_name = entry["name"]
            schema_file = self._schema_path(db_name, table_name)
 
            if not os.path.exists(schema_file):
                print(
                    f"[catalog] Warning: schema.json missing for "
                    f"{db_name}.{table_name}, skipping"
                )
                continue
 
            with open(schema_file, "r", encoding="utf-8") as f:
                schema_data = json.load(f)
 
            schema = _dict_to_schema(schema_data["columns"])
            meta = TableMeta(
                name=table_name,
                schema=schema,
                tbl_path=self._tbl_path(db_name, table_name),
                created_at=entry.get("created_at", ""),
            )
            self._cache[db_name][table_name] = meta


    #TABLE OPERATIONS
    def create_table(self, table_name: str, schema: Schema) -> TableMeta:
        db_name = self._require_current_db()
        table_name = table_name.lower()
    
        if self.table_exists(table_name):
            raise TableAlreadyExistsError(
                "Table {table_name!r} already exists in database {db_name!r}"
            )
        
        #create directory
        table_dir = self._table_dir(db_name, table_name)
        os.makedirs(table_dir, exist_ok=True)

        # write schema.json
        schema_data = {
            "table": table_name,
            "database": db_name,
            "created_at": datetime.utcnow().isoformat(),
            "columns": _schema_to_dict(schema),
        }
        with open(self._schema_path(db_name, table_name), "w", encoding="utf-8") as f:
            json.dump(schema_data, f, indent=2)

        #initialize empty .tbl file
        tbl_path = self._tbl_path(db_name, table_name)
        TableFile(tbl_path, schema)

        #Register in catalog.json
        tables = self._read_catalog(db_name)
        created_at = datetime.utcnow().isoformat()
        tables.append({"name": table_name, "created_at": created_at})
        self._write_catalog(db_name, tables)

        #add to in-memory cache
        if db_name not in self._cache:
            self._cache[db_name] = {}
        meta = TableMeta(
            name=table_name, 
            schema=schema, 
            tbl_path=tbl_path, 
            created_at=created_at, 
        )
        self._cache[db_name][table_name] = meta
        return meta

    def drop_table(self, table_name: str) -> None:
        db_name = self._require_current_db()
        table_name = table_name.lower()
    
        if not self.table_exists(table_name):
            raise TableNotFoundError(
                f"Table {table_name!r} does not exist in database {db_name!r}"
            )

        shutil.rmtree(self._table_dir(db_name, table_name))

        tables = [
            t for t in self._read_catalog(db_name) if t["name"] != table_name
        ]
        self._write_catalog(db_name, tables)

        self._cache[db_name].pop(table_name, None)

    def rename_table(self, old_name: str, new_name: str) -> None:
        db_name = self._require_current_db()
        old_name = old_name.lower()
        new_name = new_name.lower()
    
        if not self.table_exists(old_name):
            raise TableNotFoundError(f"Table {old_name!r} does not exist")
        if self.table_exists(new_name):
            raise TableAlreadyExistsError(f"Table {new_name!r} already exists")
    
            # Move the directory on disk
        old_dir = self._table_dir(db_name, old_name)
        new_dir = self._table_dir(db_name, new_name)
        os.rename(old_dir, new_dir)
    
            # Update catalog.json entry
        tables = self._read_catalog(db_name)
        for t in tables:
            if t["name"] == old_name:
                t["name"] = new_name
        self._write_catalog(db_name, tables)
    
            # Update schema.json (it records the table's own name)
        schema_path = os.path.join(new_dir, "schema.json")
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_data = json.load(f)
        schema_data["table"] = new_name
        with open(schema_path, "w", encoding="utf-8") as f:
            json.dump(schema_data, f, indent=2)
    
            # Rebuild the cache entry under the new name
        old_meta = self._cache[db_name].pop(old_name)
        new_meta = TableMeta(
            name=new_name,
            schema=old_meta.schema,
            tbl_path=self._tbl_path(db_name, new_name),
            created_at=old_meta.created_at,
        )
        self._cache[db_name][new_name] = new_meta


    def get_table(self, table_name: str, db_name: Optional[str] = None) -> TableMeta:
        db_name = (db_name or self._require_current_db()).lower()
        table_name = table_name.lower()
    
        if db_name not in self._cache:
            if not self.database_exists(db_name):
                raise DatabaseNotFoundError(f"Database {db_name!r} does not exist")
            self._load_db_cache(db_name)
    
        if table_name not in self._cache[db_name]:
            raise TableNotFoundError(
                f"Table {table_name!r} does not exist in database {db_name!r}"
            )
        return self._cache[db_name][table_name]
    
    def table_exists(self, table_name: str, db_name: Optional[str] = None) -> bool:
        try:
            self.get_table(table_name, db_name)
            return True
        except (TableNotFoundError, DatabaseNotFoundError, CatalogError):
            return False
    
    def list_tables(self, db_name: Optional[str] = None) -> list[TableMeta]:
        db_name = (db_name or self._require_current_db()).lower()
        if db_name not in self._cache:
            self._load_db_cache(db_name)
        return list(self._cache.get(db_name, {}).values())
    
    def get_table_file(self, table_name: str, db_name: Optional[str] = None) -> TableFile:
        return self.get_table(table_name, db_name).open_table()
    
    def truncate_table(self, table_name: str) -> None:
        meta = self.get_table(table_name)
        tf = meta.open_table()
        tf.truncate()


    #PRETTY PRINT HELPERS (used by CLI)
    def describe_table(self, table_name: str) -> list[dict]:
        meta = self.get_table(table_name)
        rows = []
        for col in meta.schema.columns:
            rows.append({
                "column":      col.name,
                "type":        repr(col.data_type),
                "nullable":    "YES" if col.nullable else "NO",
                "primary_key": "YES" if col.primary_key else "",
                "unique":      "YES" if col.unique else "",
            })
        return rows
    
    def __repr__(self) -> str:
        dbs = [d["name"] for d in self.list_databases()]
        return (
            f"Catalog(data_dir={self.data_dir!r}, "
            f"databases={dbs}, "
            f"current_db={self._current_db!r})"
        )