from fastapi import APIRouter, status
from app.schemas.item_schema import Item
from app.services import task_service

router = APIRouter(
    prefix="/tasks",
    tags=["Tasks"],
)

@router.post("/process_item", status_code=status.HTTP_202_ACCEPTED)
def process_item_task(item: Item):
    """
    This endpoint receives an item, and instead of processing it directly,
    it sends a task to the Celery queue via the task service.
    """
    task_id = task_service.create_processing_task(item.dict())
    return {"message": "Task accepted. The item is being processed in the background.", "task_id": task_id}

@router.get("/result/{task_id}")
def get_task_result_controller(task_id: str):
    """
    This endpoint allows clients to check the status and result of a
    previously submitted task using its ID, by calling the task service.
    """
    return task_service.get_task_result(task_id)
