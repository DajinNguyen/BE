from pydantic import BaseModel


class Term(BaseModel):
    id: str
    name: str
    aliases: list[str]
    easy_meaning: str
    example: str
