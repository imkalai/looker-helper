"""Browse BigQuery: projects, datasets, tables, and table suggestions for a request."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.services import planner
from app.services.cortex import get_cortex
from app.services.metadata import get_metadata_provider
from app.services.text import format_count

router = APIRouter(prefix="/api/meta", tags=["bigquery"])


@router.get("/projects")
def projects(_: CurrentUser = Depends(get_current_user)):
    return [p.model_dump() for p in get_metadata_provider().list_projects()]


@router.get("/projects/{project}/datasets")
def datasets(project: str, _: CurrentUser = Depends(get_current_user)):
    return [d.model_dump() for d in get_metadata_provider().list_datasets(project)]


@router.get("/projects/{project}/datasets/{dataset}/tables")
def tables(project: str, dataset: str, _: CurrentUser = Depends(get_current_user)):
    return [
        {**t.model_dump(), "rows_text": f"{format_count(t.row_count)} rows" if t.row_count is not None else ""}
        for t in get_metadata_provider().list_tables(project, dataset)
    ]


class SuggestIn(BaseModel):
    project: str
    dataset: str
    request: str


@router.post("/suggest-tables")
def suggest(body: SuggestIn, db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)):
    provider = get_metadata_provider()
    infos = provider.list_tables(body.project, body.dataset)
    if not body.request.strip() or not infos:
        return {"tables": []}
    metas = provider.get_tables(body.project, body.dataset, [i.name for i in infos[:60]])
    return {"tables": planner.suggest_tables(get_cortex(db), body.request, metas, infos)}
