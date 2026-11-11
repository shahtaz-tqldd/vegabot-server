from django.urls import include, path
from knowledge.api.v1.client import views

# 1. Base knowledge sources: files, URLs, and raw text
base_patterns = [
    path("upload-file/", views.KnowledgeDocumentUploadView.as_view(), name="knowledge-base-upload-file"),
    path("upload-url/", views.KnowledgeDocumentWebsiteView.as_view(), name="knowledge-base-upload-url"),
    path("upload-text/", views.KnowledgeDocumentTextView.as_view(), name="knowledge-base-upload-text"),
    path("list/", views.KnowledgeDocumentListView.as_view(), name="knowledge-base-list"),
    path("retrain/", views.KnowledgeDocumentRetrainView.as_view(), name="knowledge-base-retrain"),
    path("delete/", views.KnowledgeDocumentDeleteView.as_view(), name="knowledge-base-delete"),
]

# 2. Products: template, import, list, disable, retrain-on-update, bulk delete, bulk retrain
product_patterns = [
    path("template/", views.ProductTemplateView.as_view(), name="product-template"),
    path("import/", views.ProductUploadView.as_view(), name="product-import"),
    path("list/", views.ProductListView.as_view(), name="product-list"),
    path("disable/", views.ProductDisableView.as_view(), name="product-disable"),
    path("update/", views.ProductUpdateView.as_view(), name="product-update"),
    path("delete/", views.ProductBulkDeleteView.as_view(), name="product-delete"),
    path("retrain/", views.ProductBulkRetrainView.as_view(), name="product-retrain"),
    path("retrain/", views.ProductBulkRetrainView.as_view(), name="product-retrain"),
]

# 3. Services: template, import, add (single/bulk), list, remove, update
service_patterns = [
    path("template/", views.ServiceTemplateView.as_view(), name="service-template"),
    path("import/", views.ServiceUploadView.as_view(), name="service-import"),
    path("create/", views.ServiceCreateView.as_view(), name="service-create"),
    path("bulk-create/", views.ServiceBulkCreateView.as_view(), name="service-bulk-create"),
    path("list/", views.ServiceListView.as_view(), name="service-list"),
    path("update/", views.ServiceUpdateView.as_view(), name="service-update"),
    path("delete/", views.ServiceDeleteView.as_view(), name="service-delete"),
]

# 4. Master urlpatterns combining them via include()
urlpatterns = [
    path("base/", include(base_patterns)),
    path("products/", include(product_patterns)),
    path("services/", include(service_patterns)),
    # Remaining standalone endpoints
    path("logs/list/", views.TrainingLogListView.as_view(), name="knowledge-training-log-list"),
    path("search/", views.KnowledgeSearchView.as_view(), name="knowledge-search"),
]
