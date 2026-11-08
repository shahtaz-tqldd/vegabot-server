from django.urls import include, path
from knowledge.api.v1.client import views

# 1. Documents URLs grouped together
document_patterns = [
    path("upload/", views.KnowledgeDocumentUploadView.as_view(), name="knowledge-document-upload"),
    path("website/", views.KnowledgeDocumentWebsiteView.as_view(), name="knowledge-document-website"),
    path("list/", views.KnowledgeDocumentListView.as_view(), name="knowledge-document-list"),
    path("details/", views.KnowledgeDocumentDetailView.as_view(), name="knowledge-document-detail"),
    path("update/", views.KnowledgeDocumentUpdateView.as_view(), name="knowledge-document-update"),
    path("delete/", views.KnowledgeDocumentDeleteView.as_view(), name="knowledge-document-delete"),
    path("train/", views.KnowledgeDocumentTrainView.as_view(), name="knowledge-document-train"),
]

# 2. Products URLs grouped together
product_patterns = [
    path("template/", views.ProductTemplateView.as_view(), name="product-template"),
    path("upload/", views.ProductUploadView.as_view(), name="product-upload"),
    path("create/", views.ProductCreateView.as_view(), name="product-create"),
    path("list/", views.ProductListView.as_view(), name="product-list"),
    path("details/", views.ProductDetailView.as_view(), name="product-detail"),
    path("update/", views.ProductUpdateView.as_view(), name="product-update"),
    path("delete/", views.ProductDeleteView.as_view(), name="product-delete"),
    path("train/", views.ProductTrainView.as_view(), name="product-train"),
]

# 3. Services URLs grouped together
service_patterns = [
    path("create/", views.ServiceCreateView.as_view(), name="service-create"),
    path("bulk-create/", views.ServiceBulkCreateView.as_view(), name="service-bulk-create"),
    path("list/", views.ServiceListView.as_view(), name="service-list"),
    path("details/", views.ServiceDetailView.as_view(), name="service-detail"),
    path("update/", views.ServiceUpdateView.as_view(), name="service-update"),
    path("delete/", views.ServiceDeleteView.as_view(), name="service-delete"),
    path("train/", views.ServiceTrainView.as_view(), name="service-train"),
]

# 4. Master urlpatterns combining them via include()
urlpatterns = [
    path("documents/", include((document_patterns, "documents"))),
    path("products/", include((product_patterns, "products"))),
    path("services/", include((service_patterns, "services"))),
    # Remaining standalone endpoints
    path("logs/list/", views.TrainingLogListView.as_view(), name="knowledge-training-log-list"),
    path("search/", views.KnowledgeSearchView.as_view(), name="knowledge-search"),
]