"""
URL configuration for gestion_etiquetas_qr project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.views.generic import TemplateView

from modulo_gestion_qr import views
from modulo_gestion_qr.forms import CustomLoginForm
from modulo_gestion_qr.views import (
    ClienteCreateView,
    ClienteSuccessView,
    ProductoCreateView,
    ProductoSuccessView,
    ProductoUpdateView,
    obtener_templates_por_cliente,
    index,
    ver_informacion_qr,
    ver_seriales,
    dashboard,
    custom_logout,
    exportar_csv,
    crear_solicitud,
)

from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('dashboard/', dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(
        template_name='login.html', 
        authentication_form=CustomLoginForm
    ), name='login'),  # Vista personalizada para login
    path('logout/', custom_logout, name='logout'),  # Vista de logout personalizada
    path('', views.generar_seriales, name='home'),  # Redirige raíz a asociar_seriales
    #path('seriales', views.ver_seriales, name='ver_seriales'),
    path('asociar/', views.generar_seriales, name='generar_seriales'),
    path('success/', views.serial_success, name='serial_success'),
    path('<str:cliente_slug>/qr/', views.ver_informacion_qr, name='ver_informacion_qr'),
    path('cliente/nuevo/', ClienteCreateView.as_view(), name='crear_cliente'),
    path('cliente/exito/<int:pk>/', ClienteSuccessView.as_view(), name='cliente_success'),
    path('producto/nuevo/', ProductoCreateView.as_view(), name='crear_producto'),
    path('producto/exito/<int:pk>/', ProductoSuccessView.as_view(), name='producto_success'),
    path('index/', index, name='index'),  # Define la vista principal
    #path('actualizar_seriales/', views.actualizar_seriales, name='actualizar_seriales'),
    path('api/productos/<int:cliente_id>/', views.productos_por_cliente, name='productos_por_cliente'),
    path('actualizar/', views.asociar_seriales, name='asociar_seriales'),
    path('actualizar-exito/', views.asociar_seriales, name='asociar_exito'),
    path('buscar/', views.buscar_seriales, name='buscar_seriales'),
    path('cargar-productos/<int:cliente_id>/', views.productos_por_cliente, name='cargar_productos'),
    path('clientes/', views.listado_clientes, name='listado_clientes'),
    path('productos/', views.listado_productos, name='listado_productos'),
    path('crear-template/', views.crear_template_cliente, name='crear_template_cliente'),
    path('listado-templates/', views.listado_templates, name='listado_templates'),  
    path('api/obtener_campos_seriales/', views.obtener_campos_seriales, name='obtener_campos_seriales'),
    path('producto/editar/<int:pk>/', views.editar_producto, name='editar_producto'),
    path('api/templates/<int:cliente_id>/', obtener_templates_por_cliente, name='obtener_templates_por_cliente'),
    path('exportar_csv/', exportar_csv, name='exportar_csv'),
    path('solicitud/nueva/', views.crear_solicitud, name='crear_solicitud'),
    path('landing/<str:codigo>/', views.landing_solicitud, name='landing_solicitud'),
    path('solicitud/<int:solicitud_id>/editar/', views.editar_solicitud, name='editar_solicitud'),
    path('solicitud/buscar/', views.buscar_solicitud, name='buscar_solicitud'),
    path('solicitud/ver/<int:solicitud_id>/', views.ver_solicitud, name='ver_solicitud'),
    path('cinta/<str:serial>/', views.landing_serial_qr, name='landing_serial_qr'),
    path('entrega/', views.formulario_entrega, name='formulario_entrega'),
    path('api/solicitud_por_rango/', views.solicitud_por_rango, name='solicitud_por_rango'),
    path("buscar-nit/", views.buscar_nit, name="buscar_nit"),
    path('<slug:cliente_slug>/', views.crear_template_cliente, name='template_cliente'),


]

if settings.DEBUG and not settings.USE_S3:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)









