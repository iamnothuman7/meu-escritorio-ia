from django.shortcuts import redirect, render
from django.views.decorators.http import require_safe

from .studio_scope import supports_studio
from .views import active_workspace, office_required


@office_required
@require_safe
def office_3d(request):
    workspace = active_workspace(request)
    if not supports_studio(workspace):
        return redirect('office_selector')
    return render(request, 'office/studio.html', {'workspace': workspace})
