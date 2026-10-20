from django.urls import path

from . import views

urlpatterns = [
    path("geocode", views.geocode_view, name="geocode"),
    path("plan", views.plan_view, name="plan"),
    path("weather", views.weather_view, name="weather"),
]
