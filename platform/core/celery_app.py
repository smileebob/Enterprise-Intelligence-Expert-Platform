"""Celery 应用（规则 B-06/F-04；开发 eager 同步执行，生产 Valkey broker + worker 分离部署）。"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery("angentplatform", broker=settings.CELERY_BROKER_URL)
celery_app.conf.update(
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=True,
    enable_utc=True,
    timezone="UTC",
    task_default_queue="angentplatform",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        # 记忆衰减与压缩（BR-07，周期任务）
        "memory-maintenance": {
            "task": "core.tasks.memory_maintenance",
            "schedule": crontab(day_of_week="mon", hour=3, minute=0),
        }
    },
)

celery_app.autodiscover_tasks(["core"], related_name="tasks", force=True)
from core import tasks  # noqa: E402,F401 - 显式导入任务模块完成注册
