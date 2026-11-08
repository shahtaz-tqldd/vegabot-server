from celery import shared_task

from knowledge.models import KnowledgeTrainingLog
from knowledge.services.training import run_training


@shared_task(name="knowledge.tasks.run_knowledge_training")
def run_knowledge_training_task(training_log_id):
    training_log = KnowledgeTrainingLog.objects.get(pk=training_log_id)
    run_training(training_log)
    return {
        "training_log_id": str(training_log.id),
        "space": training_log.space,
    }
