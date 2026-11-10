import hashlib
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.services.web_scrapper import WebScraper
from knowledge.models import KnowledgeBase, KnowledgeTrainingLog
from knowledge.services.catalog import (
    build_product_metadata,
    build_product_text,
    build_service_metadata,
    build_service_text,
)
from knowledge.services.extraction import extract_file_content
from knowledge.services.validation import validate_public_url
from knowledge.utils.choices import (
    KnowledgeSourceTypes,
    KnowledgeSpaceTypes,
    KnowledgeTrainingStageTypes,
    StatusTypes,
)
from notification.services import create_chatbot_notification
from vector_store.models import VectorDocument
from vector_store.services.gemini_embeddings import GeminiEmbeddingService
from vector_store.services.vectorize import KnowledgeVectorService

logger = logging.getLogger(__name__)


def content_digest(content):
    normalized = (content or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def split_knowledge_content(content):
    """Split by model tokens into 400-token chunks with 50-token overlap."""

    from langchain_text_splitters import TokenTextSplitter

    splitter = TokenTextSplitter(
        encoding_name="cl100k_base",
        chunk_size=settings.KNOWLEDGE_CHUNK_SIZE,
        chunk_overlap=settings.KNOWLEDGE_CHUNK_OVERLAP,
        strip_whitespace=True,
    )
    return [chunk for chunk in splitter.split_text(content) if chunk.strip()]


def token_count(content):
    import tiktoken

    return len(tiktoken.get_encoding("cl100k_base").encode(content))


def queue_training(training_log):
    """Create the durable record (already done) and submit work to Celery.

    Falls back to a synchronous run when no broker is reachable, so the
    API keeps working in local and test environments.
    """
    from knowledge.tasks import run_knowledge_training_task

    try:
        result = run_knowledge_training_task.delay(str(training_log.pk))
        training_log.celery_task_id = result.id or ""
        training_log.save(update_fields=["celery_task_id", "updated_at"])
    except Exception:
        logger.warning(
            "Celery unavailable; training log %s runs synchronously.",
            training_log.pk,
        )
        run_training(training_log)
    return training_log


def run_training(training_log):
    if training_log.space == KnowledgeSpaceTypes.DOCUMENT:
        return KnowledgeTrainingService().run(training_log)
    return CatalogTrainingService().run(training_log)


def update_training_log(training_log, **values):
    values["updated_at"] = timezone.now()
    KnowledgeTrainingLog.objects.filter(pk=training_log.pk).update(**values)
    for key, value in values.items():
        setattr(training_log, key, value)


def notify_training_complete(chatbot, training_log, *, stage, summary):
    try:
        create_chatbot_notification(
            chatbot=chatbot,
            title="Knowledge training complete",
            message=summary,
            metadata={
                "training_log_id": str(training_log.id),
                "space": training_log.space,
                "training_stage": stage,
            },
        )
    except Exception:
        logger.exception(
            "Failed to create training-complete notification for log %s",
            training_log.id,
        )


class KnowledgeTrainingService:
    """Train one document source (file or website) into the document space."""

    def __init__(
        self,
        *,
        embedding_service=None,
        storage=None,
        scraper=None,
        vector_service=None,
    ):
        self.embedding_service = embedding_service
        self.storage = storage
        self.scraper = scraper or WebScraper()
        self.vector_service = vector_service or KnowledgeVectorService()

    def _get_embedding_service(self):
        if self.embedding_service is None:
            self.embedding_service = GeminiEmbeddingService()
        return self.embedding_service

    def run(self, training_log):
        knowledge_base = (
            KnowledgeBase.objects.select_related("chatbot").get(
                pk=training_log.knowledge_base_id,
            )
        )
        self._start(knowledge_base, training_log)
        try:
            content, scraped_title = self._extract(knowledge_base)
            title_from_scrape = ""
            if scraped_title and not knowledge_base.title:
                title_from_scrape = scraped_title[:500]
                knowledge_base.title = title_from_scrape
            new_hash = content_digest(content)
            changed = new_hash != knowledge_base.content_hash
            training_log.content_changed = changed
            training_log.save(update_fields=["content_changed", "updated_at"])

            if (
                knowledge_base.content_hash
                and not changed
                and not training_log.force_retrain
                and self.vector_service.has_complete_vector_set(
                    knowledge_base.id,
                )
            ):
                self._complete_without_changes(knowledge_base, training_log)
                return training_log

            update_training_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.CHUNKING,
                progress=25,
                message="Splitting extracted content into token-aware chunks.",
            )
            chunks = split_knowledge_content(content)
            if not chunks:
                raise ValueError("No trainable text was found in this source.")

            training_log.total_chunks = len(chunks)
            training_log.save(update_fields=["total_chunks", "updated_at"])
            documents = self._embed_chunks(
                knowledge_base,
                training_log,
                chunks,
                content_hash=new_hash,
            )

            update_training_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.INDEXING,
                progress=95,
                message="Replacing the source vectors.",
            )
            with transaction.atomic():
                self.vector_service.replace_knowledge_base(
                    knowledge_base.id,
                    documents,
                )
                completed_at = timezone.now()
                update_fields = {
                    "content_hash": new_hash,
                    "status": StatusTypes.READY,
                    "processed_at": completed_at,
                    "error_message": "",
                    "updated_at": completed_at,
                }
                if title_from_scrape:
                    update_fields["title"] = title_from_scrape
                KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
                    **update_fields,
                )
                update_training_log(
                    training_log,
                    stage=KnowledgeTrainingStageTypes.COMPLETED,
                    progress=100,
                    processed_chunks=len(chunks),
                    message=f"Trained {len(chunks)} vector chunks.",
                    completed_at=completed_at,
                )
            notify_training_complete(
                knowledge_base.chatbot,
                training_log,
                stage=KnowledgeTrainingStageTypes.COMPLETED,
                summary=f'"{knowledge_base.name}" is trained and ready to use.',
            )
            return training_log
        except Exception as exc:
            logger.exception(
                "Knowledge training failed for source %s", knowledge_base.id,
            )
            self._fail(knowledge_base, training_log, exc)
            raise

    def _start(self, knowledge_base, training_log):
        now = timezone.now()
        KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
            status=StatusTypes.PROCESSING,
            error_message="",
            updated_at=now,
        )
        update_training_log(
            training_log,
            stage=KnowledgeTrainingStageTypes.EXTRACTING,
            progress=5,
            message="Extracting source content.",
            started_at=now,
        )

    def _extract(self, knowledge_base):
        import asyncio

        if knowledge_base.source_type == KnowledgeSourceTypes.FILE:
            return extract_file_content(knowledge_base, storage=self.storage), ""
        if knowledge_base.source_type == KnowledgeSourceTypes.TEXT:
            content = (knowledge_base.extracted_content or "").strip()
            if not content:
                raise ValueError("The text source is empty.")
            return content, ""
        if knowledge_base.source_type == KnowledgeSourceTypes.WEBSITE:
            validate_public_url(knowledge_base.url)
            result = asyncio.run(
                self.scraper.scrape_text_content(knowledge_base.url),
            )
            if not result.success:
                raise ValueError(result.error or "Website extraction failed.")
            KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
                last_crawled_at=timezone.now(),
            )
            return (result.data.text or "").strip(), result.data.title or ""
        raise ValueError("Unsupported knowledge source type.")

    def _embed_chunks(self, knowledge_base, training_log, chunks, *, content_hash):
        total = len(chunks)

        def on_progress(processed, total):
            update_training_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.EMBEDDING,
                progress=30 + int((processed / total) * 60),
                processed_chunks=processed,
                message=f"Embedded {processed} of {total} chunks.",
            )

        update_training_log(
            training_log,
            stage=KnowledgeTrainingStageTypes.EMBEDDING,
            progress=30,
            message=f"Embedding {total} chunks.",
        )
        embeddings = self._get_embedding_service().embed_documents(
            chunks,
            on_progress=on_progress,
        )
        base_metadata = {
            "knowledge_base_id": str(knowledge_base.id),
            "chatbot_id": str(knowledge_base.chatbot_id),
            "title": knowledge_base.title,
            "url": knowledge_base.url,
            "original_filename": knowledge_base.original_filename,
            "source_type": knowledge_base.source_type,
            "chunk_count": total,
        }
        return [
            VectorDocument(
                chatbot=knowledge_base.chatbot,
                space=KnowledgeSpaceTypes.DOCUMENT,
                knowledge_base=knowledge_base,
                chunk_index=index,
                token_count=token_count(chunk),
                content_hash=content_hash,
                content=chunk,
                metadata={**base_metadata, "chunk_index": index},
                embedding=embedding,
            )
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))
        ]

    @staticmethod
    def _complete_without_changes(knowledge_base, training_log):
        now = timezone.now()
        KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
            status=StatusTypes.READY,
            error_message="",
            processed_at=now,
            updated_at=now,
        )
        update_training_log(
            training_log,
            stage=KnowledgeTrainingStageTypes.SKIPPED,
            progress=100,
            message="Source content is unchanged; existing vectors were retained.",
            completed_at=now,
        )

    @staticmethod
    def _fail(knowledge_base, training_log, exc):
        now = timezone.now()
        error = str(exc)[:4000]
        KnowledgeBase.objects.filter(pk=knowledge_base.pk).update(
            status=StatusTypes.FAILED,
            error_message=error,
            updated_at=now,
        )
        update_training_log(
            training_log,
            stage=KnowledgeTrainingStageTypes.FAILED,
            message="Training failed.",
            error_message=error,
            completed_at=now,
        )


class CatalogTrainingService:
    """Train a chatbot's pending products or services into their space.

    Each catalog item becomes a single structured vector; a batch run
    retrains exactly the items that are pending (created or edited).
    """

    def __init__(self, *, embedding_service=None, vector_service=None):
        self.embedding_service = embedding_service
        self.vector_service = vector_service or KnowledgeVectorService()

    def _get_embedding_service(self):
        if self.embedding_service is None:
            self.embedding_service = GeminiEmbeddingService()
        return self.embedding_service

    def run(self, training_log):
        from knowledge.models import Product, Service

        chatbot = training_log.chatbot
        if training_log.space == KnowledgeSpaceTypes.PRODUCT:
            model, text_builder, metadata_builder = (
                Product,
                build_product_text,
                build_product_metadata,
            )
        else:
            model, text_builder, metadata_builder = (
                Service,
                build_service_text,
                build_service_metadata,
            )

        queryset = model.objects.filter(
            chatbot=chatbot,
            status=StatusTypes.PENDING,
        ).order_by("created_at")
        total = queryset.count()
        update_training_log(
            training_log,
            stage=KnowledgeTrainingStageTypes.EMBEDDING,
            progress=5,
            message=f"Embedding {total} pending items.",
            started_at=timezone.now(),
        )
        training_log.total_chunks = total
        training_log.save(update_fields=["total_chunks", "updated_at"])

        if total == 0:
            update_training_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.SKIPPED,
                progress=100,
                message="No pending items to train.",
                completed_at=timezone.now(),
            )
            return training_log

        try:
            items = list(queryset)
            contents = [text_builder(item) for item in items]

            def on_progress(processed, total):
                update_training_log(
                    training_log,
                    progress=10 + int((processed / total) * 80),
                    processed_chunks=processed,
                    message=f"Embedded {processed} of {total} items.",
                )

            embeddings = self._get_embedding_service().embed_documents(
                contents,
                on_progress=on_progress,
            )
            source_field = (
                "product"
                if training_log.space == KnowledgeSpaceTypes.PRODUCT
                else "service"
            )
            documents = [
                VectorDocument(
                    chatbot=chatbot,
                    space=training_log.space,
                    knowledge_base=None,
                    **{source_field: item},
                    chunk_index=0,
                    token_count=token_count(content),
                    content_hash=content_digest(content),
                    content=content,
                    metadata=metadata_builder(item),
                    embedding=embedding,
                )
                for item, content, embedding in zip(items, contents, embeddings)
            ]
            item_ids = [item.pk for item in items]

            update_training_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.INDEXING,
                progress=95,
                message="Replacing item vectors.",
            )
            with transaction.atomic():
                self.vector_service.replace_catalog_items(
                    space=training_log.space,
                    item_ids=item_ids,
                    documents=documents,
                )
                model.objects.filter(pk__in=item_ids).update(
                    status=StatusTypes.READY,
                    error_message="",
                )
                update_training_log(
                    training_log,
                    stage=KnowledgeTrainingStageTypes.COMPLETED,
                    progress=100,
                    processed_chunks=total,
                    message=f"Trained {total} items.",
                    completed_at=timezone.now(),
                )
            notify_training_complete(
                chatbot,
                training_log,
                stage=KnowledgeTrainingStageTypes.COMPLETED,
                summary=f"Trained {total} {training_log.space} items.",
            )
            return training_log
        except Exception as exc:
            logger.exception(
                "Catalog training failed for chatbot %s (%s)",
                chatbot.pk,
                training_log.space,
            )
            update_training_log(
                training_log,
                stage=KnowledgeTrainingStageTypes.FAILED,
                message="Training failed.",
                error_message=str(exc)[:4000],
                completed_at=timezone.now(),
            )
            raise
