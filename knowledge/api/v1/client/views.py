from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse
from chatbot.models import Chatbot
from knowledge.api.v1.client.serializers import (
    CatalogQuerySerializer,
    ChatbotQuerySerializer,
    KnowledgeDocumentQuerySerializer,
    KnowledgeDocumentSerializer,
    KnowledgeDocumentUpdateSerializer,
    KnowledgeDocumentUploadSerializer,
    KnowledgeDocumentWebsiteSerializer,
    KnowledgeSearchSerializer,
    KnowledgeTrainSerializer,
    ProductCreateSerializer,
    ProductSerializer,
    ProductUpdateSerializer,
    ProductUploadSerializer,
    ServiceBulkCreateSerializer,
    ServiceCreateSerializer,
    ServiceSerializer,
    ServiceUpdateSerializer,
    TrainingLogSerializer,
)
from knowledge.models import (
    KnowledgeBase,
    KnowledgeTrainingLog,
    Product,
    Service,
)
from knowledge.services.catalog import (
    ProductImportError,
    build_product_template_csv,
    parse_product_upload,
)
from knowledge.services.search import search_knowledge
from knowledge.services.training import queue_training
from knowledge.utils.choices import (
    KnowledgeSpaceTypes,
    KnowledgeTrainingStageTypes,
    StatusTypes,
)
from vector_store.services.vectorize import KnowledgeVectorService


def manageable_chatbots(user):
    queryset = Chatbot.objects.filter(is_deleted=False)
    if not user.is_superuser:
        queryset = queryset.filter(created_by=user)
    return queryset


def get_manageable_chatbot(user, slug):
    return get_object_or_404(manageable_chatbots(user), slug=slug)


def validation_error_response(errors, message):
    return APIResponse.error(
        errors=errors,
        message=message,
        status=status.HTTP_400_BAD_REQUEST,
    )


class PaginatedListMixin:
    pagination_class = CustomPagination

    def paginated_response(self, queryset, *, message):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(self.request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message=message,
        )


class KnowledgeScopedMixin:
    """Resolve the chatbot from ``?chatbot=`` for GET/DELETE endpoints."""

    def get_scoped_chatbot(self):
        query_serializer = ChatbotQuerySerializer(data=self.request.query_params)
        query_serializer.is_valid(raise_exception=True)
        return get_manageable_chatbot(
            self.request.user,
            query_serializer.validated_data["chatbot"],
        )


class KnowledgeDocumentMixin(KnowledgeScopedMixin):
    def get_document(self, chatbot):
        query_serializer = KnowledgeDocumentQuerySerializer(
            data=self.request.query_params,
        )
        query_serializer.is_valid(raise_exception=True)
        return get_object_or_404(
            KnowledgeBase.objects.filter(chatbot=chatbot),
            pk=query_serializer.validated_data["document"],
        )


def _queue_document_training(document, *, force=False):
    return queue_training(
        KnowledgeTrainingLog.objects.create(
            chatbot=document.chatbot,
            space=KnowledgeSpaceTypes.DOCUMENT,
            knowledge_base=document,
            force_retrain=force,
        ),
    )


def _queue_catalog_training(chatbot, space):
    return queue_training(
        KnowledgeTrainingLog.objects.create(
            chatbot=chatbot,
            space=space,
        ),
    )


# --------------------------------------------------------------- documents


class KnowledgeDocumentUploadView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeDocumentUploadSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chatbot = serializer.validated_data["chatbot"]
        uploaded_file = serializer.validated_data["file"]

        from knowledge.services.storage import PrivateKnowledgeStorage

        file_key = PrivateKnowledgeStorage.build_key(
            chatbot_id=chatbot.pk,
            filename=uploaded_file.name,
        )
        storage = PrivateKnowledgeStorage()
        storage.upload(uploaded_file, key=file_key)

        document = KnowledgeBase.objects.create(
            chatbot=chatbot,
            title=serializer.validated_data.get("title", ""),
            source_type="file",
            original_filename=uploaded_file.name,
            file_type=(uploaded_file.name or "").rsplit(".", 1)[-1].lower(),
            file_key=file_key,
            file_size=uploaded_file.size,
            status=StatusTypes.PENDING,
            created_by=request.user,
        )
        _queue_document_training(document)
        return APIResponse.success(
            data=KnowledgeDocumentSerializer(document).data,
            message="Document uploaded and queued for training.",
            status=status.HTTP_201_CREATED,
        )


class KnowledgeDocumentWebsiteView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeDocumentWebsiteSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chatbot = serializer.validated_data["chatbot"]

        document = KnowledgeBase.objects.create(
            chatbot=chatbot,
            title=serializer.validated_data.get("title", ""),
            source_type="website",
            url=serializer.validated_data["url"],
            status=StatusTypes.PENDING,
            created_by=request.user,
        )
        _queue_document_training(document)
        return APIResponse.success(
            data=KnowledgeDocumentSerializer(document).data,
            message="Website added and queued for training.",
            status=status.HTTP_201_CREATED,
        )


class KnowledgeDocumentListView(PaginatedListMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeDocumentSerializer

    def get_queryset(self):
        chatbot = self.get_scoped_chatbot()
        queryset = chatbot.knowledge_bases.all()
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Documents fetched successfully.",
        )


class KnowledgeDocumentDetailView(KnowledgeDocumentMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeDocumentSerializer

    def get(self, request, *args, **kwargs):
        document = self.get_document(self.get_scoped_chatbot())
        return APIResponse.success(
            data=self.get_serializer(document).data,
            message="Document fetched successfully.",
        )


class KnowledgeDocumentUpdateView(KnowledgeDocumentMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeDocumentUpdateSerializer

    def patch(self, request, *args, **kwargs):
        document = self.get_document(self.get_scoped_chatbot())
        serializer = self.get_serializer(document, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(document, field, value)
        document.updated_by = request.user
        document.save(update_fields=[*serializer.validated_data.keys(), "updated_by", "updated_at"])
        return APIResponse.success(
            data=KnowledgeDocumentSerializer(document).data,
            message="Document updated successfully.",
        )


class KnowledgeDocumentDeleteView(KnowledgeDocumentMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        chatbot = self.get_scoped_chatbot()
        document = self.get_document(chatbot)
        KnowledgeVectorService().remove_document(document.pk)
        file_key = document.file_key
        document.delete()

        if file_key:
            from django.db import transaction

            from app.services.r2 import R2Storage

            transaction.on_commit(lambda: R2Storage().delete(file_key))
        return APIResponse.success(message="Document deleted successfully.")


class KnowledgeDocumentTrainView(KnowledgeDocumentMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeTrainSerializer

    def post(self, request, *args, **kwargs):
        document = self.get_document(self.get_scoped_chatbot())
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        log = _queue_document_training(
            document,
            force=serializer.validated_data["force"],
        )
        return APIResponse.success(
            data=TrainingLogSerializer(log).data,
            message="Document training queued.",
            status=status.HTTP_202_ACCEPTED,
        )


# ---------------------------------------------------------------- products


class ProductTemplateView(GenericAPIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(
            build_product_template_csv(),
            content_type="text/csv",
        )
        response["Content-Disposition"] = (
            'attachment; filename="dison-product-template.csv"'
        )
        return response


class ProductUploadView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductUploadSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chatbot = serializer.validated_data["chatbot"]
        uploaded_file = serializer.validated_data["file"]

        try:
            products_data, errors = parse_product_upload(uploaded_file)
        except ProductImportError as exc:
            return validation_error_response(
                {"file": str(exc)},
                "Product import failed.",
            )

        existing_skus = set(
            Product.objects.filter(chatbot=chatbot)
            .exclude(sku="")
            .values_list("sku", flat=True),
        )
        created = []
        for product_data in products_data:
            sku = product_data["sku"]
            if sku and sku.lower() in {s.lower() for s in existing_skus}:
                errors.append(
                    f"Row for '{product_data['name']}': SKU '{sku}' already exists; skipped."
                )
                continue
            created.append(
                Product(
                    chatbot=chatbot,
                    created_by=request.user,
                    **product_data,
                )
            )
            if sku:
                existing_skus.add(sku)
        Product.objects.bulk_create(created)

        if created:
            _queue_catalog_training(chatbot, KnowledgeSpaceTypes.PRODUCT)

        return APIResponse.success(
            data={
                "created_count": len(created),
                "errors": errors,
            },
            message="Product import completed.",
            status=status.HTTP_201_CREATED,
        )


class ProductCreateView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.save(created_by=request.user)
        return APIResponse.success(
            data=ProductSerializer(product).data,
            message="Product created successfully.",
            status=status.HTTP_201_CREATED,
        )


class ProductListView(PaginatedListMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductSerializer

    def get_queryset(self):
        chatbot = self.get_scoped_chatbot()
        queryset = chatbot.products.all()
        search = self.request.query_params.get("search")
        if search:
            from django.db.models import Q

            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(sku__icontains=search)
                | Q(category__icontains=search),
            )
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Products fetched successfully.",
        )


class CatalogObjectMixin:
    def get_catalog_object(self, model, related_name):
        chatbot = self.get_scoped_chatbot()
        query_serializer = CatalogQuerySerializer(data=self.request.query_params)
        query_serializer.is_valid(raise_exception=True)
        object_id = query_serializer.validated_data.get(related_name)
        return get_object_or_404(
            model.objects.filter(chatbot=chatbot),
            pk=object_id,
        )


class ProductDetailView(CatalogObjectMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductSerializer

    def get(self, request, *args, **kwargs):
        product = self.get_catalog_object(Product, "product")
        return APIResponse.success(
            data=self.get_serializer(product).data,
            message="Product fetched successfully.",
        )


class ProductUpdateView(CatalogObjectMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductUpdateSerializer

    def patch(self, request, *args, **kwargs):
        product = self.get_catalog_object(Product, "product")
        serializer = self.get_serializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        product = serializer.save(
            updated_by=request.user,
            status=StatusTypes.PENDING,
        )
        return APIResponse.success(
            data=ProductSerializer(product).data,
            message="Product updated; retrain to refresh its knowledge.",
        )


class ProductDeleteView(CatalogObjectMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        product = self.get_catalog_object(Product, "product")
        KnowledgeVectorService().remove_catalog_item(
            space=KnowledgeSpaceTypes.PRODUCT,
            item_id=product.pk,
        )
        product.delete()
        return APIResponse.success(message="Product deleted successfully.")


class ProductTrainView(KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeTrainSerializer

    def post(self, request, *args, **kwargs):
        chatbot = self.get_scoped_chatbot()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        log = _queue_catalog_training(chatbot, KnowledgeSpaceTypes.PRODUCT)
        return APIResponse.success(
            data=TrainingLogSerializer(log).data,
            message="Product training queued.",
            status=status.HTTP_202_ACCEPTED,
        )


# ---------------------------------------------------------------- services


class ServiceCreateView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ServiceCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service = serializer.save(created_by=request.user)
        return APIResponse.success(
            data=ServiceSerializer(service).data,
            message="Service created successfully.",
            status=status.HTTP_201_CREATED,
        )


class ServiceBulkCreateView(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ServiceBulkCreateSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chatbot = serializer.validated_data["chatbot"]
        services = [
            Service(
                chatbot=chatbot,
                created_by=request.user,
                **item,
            )
            for item in serializer.validated_data["services"]
        ]
        created = Service.objects.bulk_create(services)
        if created:
            _queue_catalog_training(chatbot, KnowledgeSpaceTypes.SERVICE)
        return APIResponse.success(
            data={
                "created_count": len(created),
                "services": ServiceSerializer(created, many=True).data,
            },
            message="Services created and queued for training.",
            status=status.HTTP_201_CREATED,
        )


class ServiceListView(PaginatedListMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ServiceSerializer

    def get_queryset(self):
        chatbot = self.get_scoped_chatbot()
        queryset = chatbot.services.all()
        search = self.request.query_params.get("search")
        if search:
            from django.db.models import Q

            queryset = queryset.filter(
                Q(name__icontains=search) | Q(service_type__icontains=search),
            )
        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Services fetched successfully.",
        )


class ServiceDetailView(CatalogObjectMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ServiceSerializer

    def get(self, request, *args, **kwargs):
        service = self.get_catalog_object(Service, "service")
        return APIResponse.success(
            data=self.get_serializer(service).data,
            message="Service fetched successfully.",
        )


class ServiceUpdateView(CatalogObjectMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ServiceUpdateSerializer

    def patch(self, request, *args, **kwargs):
        service = self.get_catalog_object(Service, "service")
        serializer = self.get_serializer(service, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        service = serializer.save(
            updated_by=request.user,
            status=StatusTypes.PENDING,
        )
        return APIResponse.success(
            data=ServiceSerializer(service).data,
            message="Service updated; retrain to refresh its knowledge.",
        )


class ServiceDeleteView(CatalogObjectMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, *args, **kwargs):
        service = self.get_catalog_object(Service, "service")
        KnowledgeVectorService().remove_catalog_item(
            space=KnowledgeSpaceTypes.SERVICE,
            item_id=service.pk,
        )
        service.delete()
        return APIResponse.success(message="Service deleted successfully.")


class ServiceTrainView(KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeTrainSerializer

    def post(self, request, *args, **kwargs):
        chatbot = self.get_scoped_chatbot()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        log = _queue_catalog_training(chatbot, KnowledgeSpaceTypes.SERVICE)
        return APIResponse.success(
            data=TrainingLogSerializer(log).data,
            message="Service training queued.",
            status=status.HTTP_202_ACCEPTED,
        )


# ------------------------------------------------------------ logs + search


class TrainingLogListView(PaginatedListMixin, KnowledgeScopedMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = TrainingLogSerializer

    def get_queryset(self):
        chatbot = self.get_scoped_chatbot()
        queryset = chatbot.knowledge_training_logs.all()
        space_param = self.request.query_params.get("space")
        if space_param:
            queryset = queryset.filter(space=space_param)
        return queryset

    def get(self, request, *args, **kwargs):
        return self.paginated_response(
            self.get_queryset(),
            message="Training logs fetched successfully.",
        )


class KnowledgeSearchView(GenericAPIView):
    """Search a chatbot's knowledge spaces (also used to try the agent's
    retrieval without running the agent)."""

    permission_classes = [IsAuthenticated]
    serializer_class = KnowledgeSearchSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chatbot = serializer.validated_data["chatbot"]
        results = search_knowledge(
            serializer.validated_data["query"],
            chatbot=chatbot,
            space=serializer.validated_data["type"],
            limit=serializer.validated_data["limit"],
        )
        return APIResponse.success(
            data=[
                {
                    "id": result.id,
                    "space": result.space,
                    "knowledge_base_id": result.knowledge_base_id,
                    "product_id": result.product_id,
                    "service_id": result.service_id,
                    "chunk_index": result.chunk_index,
                    "content": result.content,
                    "metadata": result.metadata,
                    "score": result.rrf_score,
                }
                for result in results
            ],
            message="Knowledge search completed.",
        )
