from app.celery_app import celery_app

def create_processing_task(item_data: dict) -> str:
    """
    Sends a task to the Celery queue to process an item.
    
    :param item_data: The dictionary representation of the item.
    :return: The ID of the created task.
    """
    task = celery_app.send_task("tasks.process_item", args=[item_data])
    return task.id

def get_task_result(task_id: str) -> dict:
    """
    Checks the status and result of a Celery task.
    
    :param task_id: The ID of the task to check.
    :return: A dictionary with the task's status and result (if ready).
    """
    result = celery_app.AsyncResult(task_id)
    
    if result.ready():
        return {"status": result.status, "result": result.get()}
    
    return {"status": result.status}
def get_task_memory_read_result(task_id: str) -> dict:
    """
    Checks the status and result of a memory read task.
    
    :param task_id: The ID of the task to check.
    :return: A dictionary with the task's status and result (if ready).
    """
    result = celery_app.AsyncResult(task_id)
    
    if result.ready():
        return {"status": result.status, "result": result.get()}
    
    return {"status": result.status}

def get_task_memory_write_result(task_id: str) -> dict:
    """
    Checks the status and result of a memory write task.
    
    :param task_id: The ID of the task to check.
    :return: A dictionary with the task's status and result (if ready).
    """
    result = celery_app.AsyncResult(task_id)
    
    if result.ready():
        return {"status": result.status, "result": result.get()}
    
    return {"status": result.status}
