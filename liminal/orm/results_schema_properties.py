from __future__ import annotations

from typing import Any

from pydantic import BaseModel, PrivateAttr


class ResultsSchemaProperties(BaseModel):
    """
    This class is the validated class that is public facing, and represents the properties of a results schema properties.

    Parameters
    ----------
    name : str
        The name of the schema.
    warehouse_name : str
       The sql table name of the schema in the benchling warehouse.
    _archived : bool
        Whether the schema is archived in Benchling.
    """

    name: str
    warehouse_name: str
    _archived: bool = PrivateAttr(default=False)

    def __init__(self, **data: Any):
        super().__init__(**data)
        self._archived = data.get("_archived", False)

    def set_archived(self, value: bool) -> ResultsSchemaProperties:
        self._archived = value
        return self

    def __repr__(self) -> str:
        """Generates a string representation of the class so that it can be executed."""
        return f"{self.__class__.__name__}({', '.join([f'{k}={v.__repr__()}' for k, v in self.model_dump().items()])})"
