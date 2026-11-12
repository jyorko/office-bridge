"""Shared schema constraints for the Office Bridge boundary."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(min_length=1, max_length=4096)]
Limit = Annotated[int, Field(ge=1, le=50)]
Text = Annotated[str, Field(min_length=1, max_length=20000)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
