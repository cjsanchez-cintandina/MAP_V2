import base64
import csv
import logging
import uuid
from io import BytesIO

import bleach
from PIL import Image, UnidentifiedImageError
from botocore.exceptions import ClientError
from storages.backends.s3boto3 import S3Boto3Storage

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, TemplateView
from django.views.generic.edit import UpdateView

from .decorators import role_required
from .forms import (
    AsociarSerialesForm,
    BuscarSerialesForm,
    EntregaForm,
    ProductoForm,
    ProductoUpdateForm,
    SerialForm,
    SolicitudForm,
    TemplateClienteForm,
    #UbicacionFormSet,
    UbicacionFormSet
)
from .models import Cliente, Entrega, Producto, Serial, Solicitud, TemplateCliente
from .utils.entrega_docs import enviar_correo_entrega_sendgrid
from django.db.models import Value




from django.db.models import Max
from django.db.models.functions import Substr
from django.db.models import IntegerField
from django.db.models.functions import Cast


logger = logging.getLogger(__name__)


@login_required
def dashboard(request):
    return render(request, 'dashboard.html', {})


@login_required
def index(request):
    return render(request, 'index.html')

@login_required
def home(request):
    return render(request, 'home.html')

@login_required
@transaction.atomic
@role_required('Gestión de Seriales')
def generar_seriales(request):
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        form = SerialForm(request.POST, cliente_id=cliente_id)
        if form.is_valid():
            numero_seriales = form.cleaned_data['numero_seriales']
            cliente = form.cleaned_data['cliente']
            producto = form.cleaned_data['producto']

            # Paso 1: Obtener último serial
            ultimo_serial_obj = Serial.objects.aggregate(ultimo_serial=Max('serial'))
            ultimo_serial = int(ultimo_serial_obj['ultimo_serial']) if ultimo_serial_obj['ultimo_serial'] else 0

            # Paso 2: Reservar un rango suficientemente grande
            rango_candidato = range(ultimo_serial + 1, ultimo_serial + 1 + (numero_seriales * 2))

            # Paso 3: Identificar seriales ya existentes para evitar colisiones
            seriales_existentes = set(
                Serial.objects.filter(serial__in=rango_candidato).values_list('serial', flat=True)
            )

            # Paso 4: Filtrar seriales disponibles
            seriales_validos = [s for s in rango_candidato if s not in seriales_existentes][:numero_seriales]

            nuevos_seriales = []
            seriales_a_crear = []

            for s in seriales_validos:
                nueva_url = f"{settings.BASE_URL}/{cliente.slug}/qr/?qr={s}"
                seriales_a_crear.append(
                    Serial(
                        serial=s,
                        cliente=cliente,
                        producto=producto,
                        url=nueva_url,
                        estado='programado'
                    )
                )
                nuevos_seriales.append({
                    'url': nueva_url,
                    'estado': 'programado'
                })

            # Paso 5: Crear en bloque
            try:
                Serial.objects.bulk_create(seriales_a_crear, ignore_conflicts=True)
            except Exception as e:
                messages.error(request, f"Ocurrió un error durante la creación masiva: {str(e)}")
                return redirect('generar_seriales')

            # Paso 6: Validar resultado y almacenar en sesión
            if len(nuevos_seriales) < numero_seriales:
                messages.warning(request, "No se pudieron generar todos los seriales por conflictos. Intenta de nuevo.")
            
            request.session['nuevos_seriales'] = nuevos_seriales
            return redirect('serial_success')

    else:
        form = SerialForm()

    return render(request, 'generar_seriales.html', {'form': form})



# Vista para ver la información del QR basado en el cliente y el serial
def ver_informacion_qr(request, cliente_slug):
    serial_param = request.GET.get('qr')
    logger.debug(f"Accediendo a ver_informacion_qr con slug={cliente_slug}, qr={serial_param}")

    if not serial_param:
        logger.error("No se proporcionó un parámetro QR")
        return render(request, '404.html', {'error': 'No se proporcionó un código QR válido'}, status=404)

    try:
        logger.debug(f"Buscando cliente con slug={cliente_slug}")
        cliente_obj = get_object_or_404(Cliente, slug=cliente_slug)
        logger.debug(f"Cliente encontrado: {cliente_obj.nombre}")

        logger.debug(f"Buscando serial={serial_param} para cliente={cliente_obj.id}")
        serial_obj = get_object_or_404(
            Serial.objects.select_related('producto', 'cliente', 'solicitud')
            .prefetch_related('solicitud__ubicaciones'),
            serial=serial_param,
            cliente=cliente_obj
        )

        logger.debug(f"Serial encontrado: {serial_obj.serial}")

        solicitud = serial_obj.solicitud

        # 🔴 SI EL SERIAL NO TIENE SOLICITUD
        if not solicitud:

            producto = serial_obj.producto

            if producto and producto.template:
                template_name = producto.template.nombre
            else:
                template_name = "landing.html"

            # Si el template es crear solicitud
            if template_name == "crear_solicitud.html":
                url = reverse('crear_solicitud')
                return redirect(f"{url}?serial={serial_obj.serial}")

            # Si no aplica template especial
            return render(request, 'landing/serial_inactivo.html', {'serial': serial_obj})


        # 🟢 SI EL SERIAL YA TIENE SOLICITUD
        ubicacion = solicitud.ubicaciones.first() if solicitud else None

        logger.debug(f"Solicitud: {solicitud}, Ubicación: {ubicacion}")

        contexto = {
            'cliente': cliente_obj,
            'producto': serial_obj.producto,
            'serial': serial_obj,
            'solicitud': solicitud,
            'ubicacion': ubicacion,
        }

        return render(request, 'landing/landing_cinta.html', contexto)

    except Cliente.DoesNotExist:
        logger.error(f"Cliente con slug={cliente_slug} no encontrado")
        return render(request, '404.html', {'error': f'Cliente con slug {cliente_slug} no encontrado'}, status=404)

    except Serial.DoesNotExist:
        logger.error(f"Serial {serial_param} no encontrado para cliente {cliente_slug}")
        return render(request, '404.html', {'error': f'Serial {serial_param} no encontrado'}, status=404)

    except Exception as e:
        logger.error(f"Error inesperado en ver_informacion_qr: {str(e)}", exc_info=True)
        raise

# Vista de éxito para mostrar los nuevos seriales generados
@login_required
@role_required('Gestión de Seriales')
def serial_success(request):
    nuevos_seriales = request.session.get('nuevos_seriales', [])

    seriales_data = []
    for serial_info in nuevos_seriales:
        serial_obj = Serial.objects.filter(url=serial_info.get('url')).first()
        if serial_obj:
            seriales_data.append({
                'serial': serial_obj.serial,  # Se obtiene correctamente el serial
                'url': serial_obj.url,
                'estado': serial_obj.estado,
                'fecha_creacion': serial_obj.fecha_creacion
            })

    # Eliminar los datos de la sesión después de procesarlos
    request.session.pop('nuevos_seriales', None)

    return render(request, 'serial_success.html', {'seriales': seriales_data})

# Vista para crear un cliente
@method_decorator(role_required('Gestión de Clientes'), name='dispatch')
class ClienteCreateView(LoginRequiredMixin, CreateView):
    model = Cliente
    template_name = 'crear_cliente.html'
    fields = ['nombre', 'codigo_cliente']
    success_url = reverse_lazy('cliente_success')

    def form_valid(self, form):
        # Guarda el cliente y redirige pasando su ID
        self.object = form.save()
        return HttpResponseRedirect(reverse_lazy('cliente_success', kwargs={'pk': self.object.pk}))


# Vista para mostrar el mensaje de éxito tras la creación de un cliente
@method_decorator(role_required('Gestión de Clientes'), name='dispatch')
class ClienteSuccessView(LoginRequiredMixin, TemplateView):
    template_name = 'cliente_success.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cliente = Cliente.objects.get(pk=self.kwargs['pk'])
        context['cliente_data'] = {
            "Nombre": cliente.nombre,
            "Código Cliente": cliente.codigo_cliente,
        }
        return context


# Vista para crear un producto con la lista de clientes y templates asociados
class ProductoCreateView(LoginRequiredMixin, CreateView):
    model = Producto
    form_class = ProductoForm
    template_name = 'crear_producto.html'

    def get_success_url(self):
        return reverse_lazy('producto_success', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['clientes'] = Cliente.objects.all()
        context['range'] = range(1, 6)  # Asegurar que range se pase al template
        return context

    def form_valid(self, form):
        producto = form.save(commit=False)

        # Obtener y asignar el template seleccionado
        template_id = self.request.POST.get('template')  # ← Captura el ID del template
        if template_id:
            producto.template = TemplateCliente.objects.get(id=template_id)

        # Guardar los nombres de los campos adicionales en el modelo Producto
        producto.nombre_campo1 = self.request.POST.get('nombre_campo1')
        producto.nombre_campo2 = self.request.POST.get('nombre_campo2')
        producto.nombre_campo3 = self.request.POST.get('nombre_campo3')
        producto.nombre_campo4 = self.request.POST.get('nombre_campo4')
        producto.nombre_campo5 = self.request.POST.get('nombre_campo5')

        producto.save()  # ← Ahora el template se guarda correctamente en la base de datos
        return super().form_valid(form)



def obtener_nombres_campos(request, producto_id):
    try:
        producto = Producto.objects.get(id=producto_id)
        campos = {
            'campo1': producto.campo1,
            'campo2': producto.campo2,
            'campo3': producto.campo3,
            'campo4': producto.campo4,
            'campo5': producto.campo5,
        }
        return JsonResponse(campos)
    except Producto.DoesNotExist:
        return JsonResponse({'error': 'Producto no encontrado'}, status=404)


@method_decorator(role_required('Gestión de Productos'), name='dispatch')
class ProductoSuccessView(LoginRequiredMixin, TemplateView):
    template_name = 'producto_success.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        producto = Producto.objects.get(pk=self.kwargs['pk'])
        context['producto_data'] = {
            "Nombre": producto.nombre,
            "Código Producto": producto.codigo_producto,
            "Descripción del Producto": producto.descripcion_producto,  # Usa el nombre correcto del campo
            "Cliente": producto.cliente.nombre if producto.cliente else "Sin Cliente",
        }
        return context
    
@login_required
@role_required('Gestión de Seriales')
def ver_seriales(request):
    # Obtener el parámetro de búsqueda
    cliente_nombre = request.GET.get('cliente', '')

    # Filtrar los seriales por cliente si hay búsqueda
    if cliente_nombre:
        seriales_list = Serial.objects.filter(cliente__nombre__icontains=cliente_nombre)
    else:
        seriales_list = Serial.objects.all()

    # Paginación
    paginator = Paginator(seriales_list, 10)  # Mostrar 10 seriales por página
    page_number = request.GET.get('page')
    seriales = paginator.get_page(page_number)

    # Renderizar el template con los seriales
    return render(request, 'ver_seriales.html', {'seriales': seriales})

@login_required
@role_required('Gestión de Seriales')
def actualizar_seriales(request):
    if request.method == 'GET':
        cliente_id = request.GET.get('cliente_id', None)
        if cliente_id:
            # Filtrar los seriales asociados al cliente
            seriales = Serial.objects.filter(cliente_id=cliente_id).values(
                'serial', 'producto__nombre', 'estado', 'serial',
                'campo1', 'campo2', 'campo3', 'campo4', 'campo5'
            )
            return JsonResponse(list(seriales), safe=False)
        return JsonResponse({'error': 'Cliente no encontrado'}, status=400)

    elif request.method == 'POST':
        # Obtener datos del formulario
        cliente_id = request.POST.get('cliente')
        desde = request.POST.get('desde')
        hasta = request.POST.get('hasta')
        campo1 = request.POST.get('campo1')
        campo2 = request.POST.get('campo2')
        campo3 = request.POST.get('campo3')
        campo4 = request.POST.get('campo4')
        campo5 = request.POST.get('campo5')

        # Validar rango de seriales
        seriales = Serial.objects.filter(cliente_id=cliente_id, serial__gte=desde, serial__lte=hasta)

        # Actualizar seriales con los nuevos valores
        seriales.update(
            campo1=campo1,
            campo2=campo2,
            campo3=campo3,
            campo4=campo4,
            campo5=campo5,
        )

        return JsonResponse({'status': 'ok'})


# --- Actualizado: Obtener campos seriales con estado ---
@role_required('Gestión de Seriales')
@require_GET
def obtener_campos_seriales(request):
    desde = request.GET.get('desde')
    hasta = request.GET.get('hasta')
    solicitud_id = request.GET.get('solicitud')

    try:
        # Buscar un serial en el rango
        serial = Serial.objects.filter(
            serial__gte=desde, serial__lte=hasta
        ).first()

        # Obtener el producto asociado al serial
        producto = serial.producto if serial and serial.producto else None

        if serial and producto:
            response = {
                'nombre_campo1': producto.nombre_campo1 or 'Campo 1',
                'nombre_campo2': producto.nombre_campo2 or 'Campo 2',
                'nombre_campo3': producto.nombre_campo3 or 'Campo 3',
                'nombre_campo4': producto.nombre_campo4 or 'Campo 4',
                'nombre_campo5': producto.nombre_campo5 or 'Campo 5',
                'valor_campo1': serial.campo1 or '',
                'valor_campo2': serial.campo2 or '',
                'valor_campo3': serial.campo3 or '',
                'valor_campo4': serial.campo4 or '',
                'valor_campo5': serial.campo5 or '',
                'estado': serial.estado or 'Programado',
            }
        elif serial:
            # Si no hay producto, usar valores por defecto pero mantener el estado del serial
            response = {
                'nombre_campo1': 'Campo 1',
                'nombre_campo2': 'Campo 2',
                'nombre_campo3': 'Campo 3',
                'nombre_campo4': 'Campo 4',
                'nombre_campo5': 'Campo 5',
                'valor_campo1': serial.campo1 or '',
                'valor_campo2': serial.campo2 or '',
                'valor_campo3': serial.campo3 or '',
                'valor_campo4': serial.campo4 or '',
                'valor_campo5': serial.campo5 or '',
                'estado': serial.estado or 'Programado',
            }
        else:
            # Fallback si no hay serial
            response = {
                'nombre_campo1': 'Campo 1',
                'nombre_campo2': 'Campo 2',
                'nombre_campo3': 'Campo 3',
                'nombre_campo4': 'Campo 4',
                'nombre_campo5': 'Campo 5',
                'valor_campo1': '',
                'valor_campo2': '',
                'valor_campo3': '',
                'valor_campo4': '',
                'valor_campo5': '',
                'estado': 'Programado',
            }

        return JsonResponse(response)
    except ValueError:
        return JsonResponse({'error': 'Rango de seriales inválido.'}, status=400)


def productos_por_cliente(request, cliente_id):
    """
    Devuelve los productos relacionados con un cliente específico.
    """
    productos = Producto.objects.filter(cliente_id=cliente_id).values('id', 'nombre')
    return JsonResponse(list(productos), safe=False)


@login_required
@role_required('Gestión de Seriales')
@transaction.atomic
def asociar_seriales(request):
    if request.method == 'POST':
        form = AsociarSerialesForm(request.POST)
        if form.is_valid():
            desde = form.cleaned_data['desde']
            hasta = form.cleaned_data['hasta']
            solicitud = form.cleaned_data['solicitud']

            # Verificar si hay seriales ya asociados a otra solicitud
            seriales_con_solicitud = Serial.objects.filter(
                serial__gte=desde,
                serial__lte=hasta,
                solicitud__isnull=False
            ).exclude(solicitud=solicitud)

            if seriales_con_solicitud.exists():
                messages.warning(
                    request,
                    f"Advertencia: {seriales_con_solicitud.count()} seriales en el rango ya están asociados a otra solicitud. Se reasignarán a {solicitud.codigo}."
                )

            # Actualizar los seriales en el rango con todos los campos, incluyendo solicitud
            seriales_actualizados = Serial.objects.filter(
                serial__gte=desde,
                serial__lte=hasta
            ).update(
                solicitud=solicitud,
                campo1=form.cleaned_data['campo1'],
                campo2=form.cleaned_data['campo2'],
                campo3=form.cleaned_data['campo3'],
                campo4=form.cleaned_data['campo4'],
                campo5=form.cleaned_data['campo5'],
                estado=form.cleaned_data['estado']
            )

            if seriales_actualizados > 0:
                messages.success(
                    request,
                    f"Se asociaron o reasignaron {seriales_actualizados} seriales a la solicitud {solicitud.codigo}."
                )
                return redirect('asociar_exito')
            else:
                messages.error(request, "No se encontraron seriales en el rango especificado.")
        else:
            messages.error(request, f"Por favor, corrige los errores en el formulario: {form.errors.as_json()}")
    else:
        form = AsociarSerialesForm()

    return render(request, 'asociar_seriales.html', {
        'form': form,
    })


# --- NUEVO: inferir la solicitud a partir del rango ---


# --- Actualizado: Inferir la solicitud a partir del rango ---
@role_required('Gestión de Seriales')
def solicitud_por_rango(request):
    desde = request.GET.get('desde')
    hasta = request.GET.get('hasta')
    if not desde or not hasta:
        return JsonResponse({'error': 'Falta desde/hasta.'}, status=400)

    # Obtener todas las solicitudes de la base de datos
    todas_las_solicitudes = Solicitud.objects.all().values('id', 'codigo', 'razon_social')
    solicitudes = [
        {
            'id': s['id'],
            'label': f"{s['codigo']} - {s['razon_social']}"
        } for s in todas_las_solicitudes
    ]

    # Verificar las solicitudes asociadas al rango
    qs = Serial.objects.filter(serial__gte=desde, serial__lte=hasta).values_list('solicitud', flat=True).distinct()
    ids = [sid for sid in qs if sid]
    solicitud_seleccionada = None
    advertencia = None

    if len(ids) == 1:
        try:
            s = Solicitud.objects.get(id=ids[0])
            solicitud_seleccionada = {'id': s.id, 'label': f"{s.codigo} - {s.razon_social}"}
        except Solicitud.DoesNotExist:
            pass
    elif len(ids) > 1:
        advertencia = f"Advertencia: El rango contiene {len(ids)} solicitudes asociadas. Por favor, seleccione la solicitud deseada."

    return JsonResponse({
        'solicitudes': solicitudes,
        'solicitud_seleccionada': solicitud_seleccionada,
        'advertencia': advertencia
    }, status=200)


# Vista principal para buscar seriales
@role_required('Gestión de Seriales')
def buscar_seriales(request):
    form = BuscarSerialesForm()
    seriales = None
    nombres_campos = {}  # Para almacenar los nombres de los campos personalizados

    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')  # Obtener cliente seleccionado
        form = BuscarSerialesForm(request.POST, cliente_id=cliente_id)

        if form.is_valid():
            cliente = form.cleaned_data['cliente']
            producto = form.cleaned_data['producto']
            
            # Filtrar los seriales por cliente y producto
            seriales = Serial.objects.filter(cliente=cliente, producto=producto)

            # Obtener nombres de los campos desde el producto seleccionado
            nombres_campos = {
                'nombre_campo1': producto.nombre_campo1 or "Campo 1",
                'nombre_campo2': producto.nombre_campo2 or "Campo 2",
                'nombre_campo3': producto.nombre_campo3 or "Campo 3",
                'nombre_campo4': producto.nombre_campo4 or "Campo 4",
                'nombre_campo5': producto.nombre_campo5 or "Campo 5",
            }

    return render(request, 'buscar_seriales.html', {
        'form': form,
        'seriales': seriales,
        'nombres_campos': nombres_campos  # Pasar los nombres de los campos al template
    })


@role_required('Gestión de Clientes')
def listado_clientes(request):
    """
    Vista para listar todos los clientes.
    """
    clientes = Cliente.objects.all()  # Consulta todos los clientes de la tabla Cliente
    return render(request, 'listado_clientes.html', {'clientes': clientes})

@role_required('Gestión de Productos')
def listado_productos(request):
    """
    Vista para listar productos filtrados por cliente.
    """
    clientes = Cliente.objects.all()  # Lista de todos los clientes
    productos = None

    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        if cliente_id:
            productos = Producto.objects.filter(cliente_id=cliente_id)

    return render(request, 'listado_productos.html', {
        'clientes': clientes,
        'productos': productos
    })


@role_required('Gestión de Templates')
def crear_template_cliente(request):
    """
    Vista para crear un template asociado a un cliente.
    """
    if request.method == 'POST':
        form = TemplateClienteForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('listado_templates')  # Redirigir a una página donde se listan templates
    else:
        form = TemplateClienteForm()

    return render(request, 'crear_template_cliente.html', {'form': form})


@role_required('Gestión de Templates')
def listado_templates(request):
    """
    Vista para listar todos los templates asociados a clientes.
    """
    templates = TemplateCliente.objects.all()
    return render(request, 'listado_templates.html', {'templates': templates})



@method_decorator(login_required, name='dispatch')
class ProductoUpdateView(UpdateView):
    model = Producto
    form_class = ProductoUpdateForm
    template_name = 'actualizar_producto.html'

    def get_success_url(self):
        return reverse_lazy('producto_success', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['titulo'] = 'Actualizar Producto'
        return context


@login_required
@role_required('Gestión de Productos')
def editar_producto(request, pk):
    producto = get_object_or_404(Producto, pk=pk)
    if request.method == 'POST':
        form = ProductoForm(request.POST, instance=producto)
        if form.is_valid():
            form.save()
            return redirect('listado_productos')
    else:
        form = ProductoForm(instance=producto)

    # Lista de campos adicionales con índices comenzando en 1
    campos_adicionales = [
        {"nombre": f"nombre_campo{i}", "valor": getattr(producto, f"nombre_campo{i}", "")}
        for i in range(1, 6)
    ]
    return render(request, 'actualizar_producto.html', {
        'form': form,
        'clientes': Cliente.objects.all(),
        'campos_adicionales': campos_adicionales,
    })


def obtener_templates_por_cliente(request, cliente_id):
    """
    Devuelve los templates asociados a un cliente en formato JSON.
    """
    templates = TemplateCliente.objects.filter(cliente_id=cliente_id).values('id', 'nombre')
    return JsonResponse(list(templates), safe=False)


def custom_logout(request):
    """Cierra la sesión del usuario y elimina cookies de sesión y CSRF."""
    logout(request)
    messages.success(request, "Has cerrado sesión correctamente.")
    
    response = redirect('login')  # Redirige a la vista de login
    response.delete_cookie('csrftoken')  # Elimina la cookie CSRF
    response.delete_cookie('sessionid')  # Elimina la cookie de sesión
    return response



@role_required('Gestión de Seriales')
def exportar_csv(request):
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        producto_id = request.POST.get('producto')

        # Validación de cliente y producto
        cliente = Cliente.objects.get(id=cliente_id)
        producto = Producto.objects.get(id=producto_id)

        # Obtener seriales asociados
        seriales = Serial.objects.filter(cliente=cliente, producto=producto)

        # Preparar respuesta CSV
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="seriales.csv"'

        writer = csv.writer(response)

        # Escribir cada serial en una fila (formato: URL, SERIAL, espacio)
        for serial in seriales:
            writer.writerow([serial.url, serial.serial, ''])

        return response
    


logger = logging.getLogger('storages')




@transaction.atomic
def crear_solicitud(request):
    serial_qr = request.GET.get("serial")
    ocultar_menu = False
    if serial_qr:
        ocultar_menu = True
    else:
        if not request.user.is_authenticated:
            return redirect("login")

    if request.method == 'POST':
        solicitud_id = request.POST.get('solicitud_id')
        
        if solicitud_id:
            solicitud_existente = get_object_or_404(Solicitud, id=solicitud_id)
       
            data = request.POST.copy()
            data['codigo'] = solicitud_existente.codigo
            form = SolicitudForm(data, request.FILES, instance=solicitud_existente)
        else:
            form = SolicitudForm(request.POST, request.FILES)

        if form.is_valid():
            solicitud = form.save(commit=False)

            if 'logo' in request.FILES:
                logo_file = request.FILES['logo']
                solicitud.logo.save(logo_file.name, logo_file, save=False)

            solicitud.save()

            formset = UbicacionFormSet(
                request.POST,
                request.FILES,
                instance=solicitud
            )

            if formset.is_valid():
                try:
                    ubicaciones = formset.save(commit=False)

                    for ubicacion in ubicaciones:
                        if any([ubicacion.direccion, ubicacion.telefono, ubicacion.ciudad]):
                            ubicacion.solicitud = solicitud
                            ubicacion.save()

                    for obj in formset.deleted_objects:
                        obj.delete()

                    messages.success(request, 'Solicitud creada exitosamente.')

                    return render(request, 'crear_solicitud.html', {
                        'form': SolicitudForm(initial={'codigo': get_siguiente_codigo()}),
                        'formset': UbicacionFormSet(),
                        'exito': True,
                        'codigo_generado': solicitud.codigo,
                        'url_generada': f"{settings.BASE_URL}/landing/{solicitud.codigo}/",
                        'ocultar_menu': ocultar_menu
                    })

                except Exception as e:
                    logger.error(f"Error inesperado al crear solicitud: {e}")
                    messages.error(request, f"Error al crear la solicitud: {e}")

            else:
                messages.error(request, "Errores en las ubicaciones.")

        return render(request, 'crear_solicitud.html', {
            'form': form,
            'formset': UbicacionFormSet(request.POST, request.FILES),
            'ocultar_menu': ocultar_menu
        })

    else:

        form = SolicitudForm(initial={'codigo': get_siguiente_codigo()})
        formset = UbicacionFormSet()

    return render(request, 'crear_solicitud.html', {
        'form': form,
        'formset': formset,
        'serial_qr': serial_qr,
        'ocultar_menu': ocultar_menu
    })

def landing_solicitud(request, codigo):
    solicitud = get_object_or_404(Solicitud, codigo=codigo)
    ubicacion = solicitud.ubicaciones.first()
    return render(request, 'landing_page.html', {
        'solicitud': solicitud,
        'ubicacion': ubicacion,
    })




logger = logging.getLogger('storages')

@login_required
@role_required('Gestión de Seriales')
@transaction.atomic
def editar_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(Solicitud, id=solicitud_id)

    if request.method == 'POST':
        form = SolicitudForm(request.POST, request.FILES, instance=solicitud)
        formset = UbicacionFormSet(request.POST, instance=solicitud)

        if form.is_valid() and formset.is_valid():
            try:
                # Guardamos sin commit para poder manipular el logo de forma manual (igual que en crear)
                solicitud_actualizada = form.save(commit=False)

                # --- limpiar logo si el usuario marcó "Borrar" (ClearableFileInput) ---
                # Django usa el nombre `<field>-clear` para el checkbox de limpiar
                if request.POST.get('logo-clear'):
                    if solicitud_actualizada.logo:
                        try:
                            # borra del storage y limpia el campo
                            solicitud_actualizada.logo.storage.delete(solicitud_actualizada.logo.name)
                        except Exception:
                            pass
                    solicitud_actualizada.logo = None

                # --- si subieron un nuevo archivo, súbelo a S3 manualmente (igual que en crear) ---
                if 'logo' in request.FILES and request.FILES['logo']:
                    logo_file = request.FILES['logo']
                    logger.debug(f"[EDITAR] Nuevo logo recibido: {logo_file.name}, size: {logo_file.size}")

                    storage = S3Boto3Storage()
                    # si tu bucket permite sobreescritura = False, el storage le añadirá sufijos únicos si es necesario
                    file_path = f"logos_empresas/{logo_file.name}"
                    try:
                        storage.save(file_path, logo_file)
                        solicitud_actualizada.logo = file_path  # asignamos la ruta/clave en S3 (igual que en crear)
                        logger.debug(f"[EDITAR] Logo subido a S3 en: {file_path}")
                    except ClientError as e:
                        logger.error(f"[EDITAR] Error al subir logo a S3: {e}")
                        messages.error(request, f"Error al subir el logo a S3: {e}")
                        return render(request, 'editar_solicitud.html', {
                            'form': form,
                            'formset': formset,
                            'solicitud': solicitud
                        })

                # guarda la solicitud (esto también actualizará fecha_actualizacion)
                solicitud_actualizada.save()

                # guarda ubicaciones
                formset.instance = solicitud_actualizada
                formset.instance = solicitud_actualizada
                ubicaciones = formset.save(commit=False)

                for ubicacion in ubicaciones:
                    if any([
                        ubicacion.direccion,
                        ubicacion.telefono,
                        ubicacion.ciudad
                    ]):
                        ubicacion.solicitud = solicitud_actualizada
                        ubicacion.save()

                for obj in formset.deleted_objects:
                    obj.delete()

                messages.success(request, 'La solicitud fue actualizada exitosamente.')
                # Quédate en el mismo formulario (tu preferencia)
                return redirect('editar_solicitud', solicitud_id=solicitud.id)

            except Exception as e:
                logger.error(f"[EDITAR] Error inesperado al actualizar: {e}")
                messages.error(request, f"Ocurrió un error al actualizar: {e}")
                return render(request, 'editar_solicitud.html', {
                    'form': form,
                    'formset': formset,
                    'solicitud': solicitud
                })
        else:
            messages.error(
                request,
                f"Revisa los campos. Form: {form.errors.as_text() or 'OK'} | "
                f"Formset: {formset.errors or 'OK'}"
            )
    else:
        form = SolicitudForm(instance=solicitud)
        formset = UbicacionFormSet(instance=solicitud)

    return render(request, 'editar_solicitud.html', {
        'form': form,
        'formset': formset,
        'solicitud': solicitud
    })
    
#crear solicitud con código autogenerado

def get_siguiente_codigo():
    ultima = (
        Solicitud.objects
        .filter(codigo__startswith='CEI')
        .annotate(
            numero=Cast(Substr('codigo', 4), IntegerField())
        )
        .aggregate(max_num=Max('numero'))
    )

    siguiente = (ultima['max_num'] or 0) + 1
    return f'CEI{siguiente:04d}'

# views.py
def buscar_solicitud(request):
    codigo = request.GET.get('codigo')
    solicitud = None
    if codigo:
        solicitud = Solicitud.objects.filter(codigo__iexact=codigo).first()
    return render(request, 'buscar_solicitud.html', {'solicitud': solicitud})

@login_required
@role_required('Gestión de Seriales')
def ver_solicitud(request, solicitud_id):
    solicitud = get_object_or_404(Solicitud, id=solicitud_id)
    ubicaciones = solicitud.ubicaciones.all()  

    return render(request, 'ver_solicitud.html', {
        'solicitud': solicitud,
        'ubicaciones': ubicaciones
    })


# Vista 1: Mostrar landing de la empresa al escanear QR
def landing_serial_qr(request, serial):
    serial_obj = get_object_or_404(Serial, serial=serial)
    solicitud = serial_obj.solicitud
    ubicacion = solicitud.ubicaciones.first() if solicitud else None
    if solicitud and solicitud.sobre_nosotros:
        solicitud.sobre_nosotros = bleach.clean(solicitud.sobre_nosotros, tags=['p', 'strong', 'br'], strip=True)
    return render(request, 'landing/landing_cinta.html', {
        'serial': serial_obj,
        'solicitud': solicitud,
        'ubicacion': ubicacion
    })



# Vista 2: Mostrar formulario de entrega y guardar evidencia

# views.py


@transaction.atomic
def formulario_entrega(request):
    serial_code = request.GET.get('serial')
    if not serial_code:
        return HttpResponseBadRequest("Falta el parámetro 'serial'.")

    try:
        serial_obj = Serial.objects.select_for_update().get(serial=serial_code)
    except Serial.DoesNotExist:
        raise Http404("Serial no encontrado")

    solicitud = serial_obj.solicitud
    max_allowed = serial_obj.max_entregas
    used = serial_obj.entregas.count()
    remaining = max_allowed - used

    # Si no hay cupos y es GET
    if remaining <= 0 and request.method != 'POST':
        return render(request, 'landing/entrega_sin_cupos.html', {
            'serial': serial_obj,
            'solicitud': solicitud,
            'max_allowed': max_allowed,
            'used': used,
        })

    if request.method == 'POST':
        # Recalcula por seguridad
        used = serial_obj.entregas.count()
        if used >= max_allowed:
            return render(request, 'landing/entrega_sin_cupos.html', {
                'serial': serial_obj,
                'solicitud': solicitud,
                'max_allowed': max_allowed,
                'used': used,
            })

        foto_data = (request.POST.get('foto') or "").strip()
        firma_data = (request.POST.get('firma') or "").strip()

        def convertir_base64_a_inmemory(base64_string, nombre_base):
            """
            Devuelve InMemoryUploadedFile listo para subir (o None si inválido).
            """
            try:
                if ';base64,' not in base64_string:
                    return None
                fmt, imgstr = base64_string.split(';base64,')
                ext = fmt.split('/')[-1].lower()  # 'jpg','jpeg','png','webp', etc.
                pil_format = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP'}.get(ext, ext.upper())

                img = Image.open(BytesIO(base64.b64decode(imgstr)))
                img_io = BytesIO()
                img.save(img_io, format=pil_format)
                img_io.seek(0)

                file_name = f"{nombre_base}_{uuid.uuid4().hex[:8]}.{ext}"
                return InMemoryUploadedFile(
                    img_io, None, file_name, f'image/{ext}', img_io.getbuffer().nbytes, None
                )
            except (ValueError, UnidentifiedImageError, base64.binascii.Error):
                return None

        entrega = Entrega(
            solicitud=solicitud,
            serial=serial_obj,
            nombre=(request.POST.get('nombre') or '').strip(),
            correo=(request.POST.get('correo') or '').strip(),
            telefono=(request.POST.get('telefono') or '').strip(),
        )

        # === Subida a S3 igual que en crear_solicitud ===
        storage = S3Boto3Storage()

        f_foto = convertir_base64_a_inmemory(foto_data, 'foto')
        if f_foto:
            try:
                # Mantengo la convención de rutas que ya tienes
                path_foto = f"entregas/fotos/{f_foto.name}"
                storage.save(path_foto, f_foto)
                # Igual que en crear_solicitud: asigna la ruta al campo
                entrega.foto = path_foto
            except ClientError as e:
                messages.error(request, f"Error al subir la foto a S3: {e}")
                return render(request, 'landing/formulario_entrega.html', {
                    'serial': serial_obj,
                    'solicitud': solicitud,
                    'remaining': remaining,
                    'max_allowed': max_allowed,
                })

        f_firma = convertir_base64_a_inmemory(firma_data, 'firma')
        if f_firma:
            try:
                path_firma = f"entregas/firmas/{f_firma.name}"
                storage.save(path_firma, f_firma)
                entrega.firma = path_firma
            except ClientError as e:
                messages.error(request, f"Error al subir la firma a S3: {e}")
                return render(request, 'landing/formulario_entrega.html', {
                    'serial': serial_obj,
                    'solicitud': solicitud,
                    'remaining': remaining,
                    'max_allowed': max_allowed,
                })
        # === fin subida S3 ===

        entrega.save()

        # === NUEVO: Enviar correo con PDF (SendGrid Web API) ===
        try:
            enviar_correo_entrega_sendgrid(
                entrega,
                from_email="cintainteligente@gmail.com",
                cc_usuario=False,  # cambia a True si quieres copiar al correo que diligenció el usuario
            )
        except Exception as e:
            # No romper el flujo si el email falla
            messages.warning(request, f"Entrega registrada, pero falló el envío de correo: {e}")

        return render(request, 'landing/entrega_exitosa.html', {
            'entrega': entrega,
            'solicitud': solicitud,
        })

    # GET
    return render(request, 'landing/formulario_entrega.html', {
        'serial': serial_obj,
        'solicitud': solicitud,
        'remaining': remaining,
        'max_allowed': max_allowed,
    })


#buscar nit para autocompletar datos en el formulario de entrega

def buscar_nit(request):
    nit = request.GET.get('nit', '').strip()
    if not nit:
        return JsonResponse({'existe': False})

    try:
        solicitud = Solicitud.objects.filter(nit=nit).latest('id')
        
        ubicaciones = []
        for ub in solicitud.ubicaciones.all():
            ubicaciones.append({
                'direccion': ub.direccion or '',
                'telefono':  ub.telefono  or '',
                'ciudad':    ub.ciudad    or '',
            })

        return JsonResponse({
            'existe':         True,
            'codigo':         solicitud.codigo           or '',  # ← nuevo
            'solicitud_id':   solicitud.id,                      # ← nuevo
            'razon_social':   solicitud.razon_social     or '',
            'correo':         solicitud.correo           or '',
            'pagina_web':     solicitud.pagina_web       or '',
            'celular':        solicitud.celular          or '',
            'sobre_nosotros': solicitud.sobre_nosotros   or '',
            'link_adicional': solicitud.link_adicional   or '',
            'logo_url': (lambda: solicitud.logo.url if solicitud.logo and str(solicitud.logo) else '')(),
            'logo_nombre':    solicitud.logo.name.split('/')[-1] if solicitud.logo else '',
            'ubicaciones':    ubicaciones,
        })

    except Solicitud.DoesNotExist:
        return JsonResponse({'existe': False})