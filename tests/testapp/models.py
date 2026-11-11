from django.db import models


class Team(models.Model):
    name = models.CharField(max_length=50)


class Device(models.Model):
    name = models.CharField(max_length=50)
    status = models.CharField(max_length=20)
    size = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    created = models.DateTimeField(null=True)
    team = models.ForeignKey(Team, null=True, on_delete=models.CASCADE)
    meta = models.JSONField(default=dict)


class Member(models.Model):
    name = models.CharField(max_length=50)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    user = models.ForeignKey("auth.User", null=True, on_delete=models.SET_NULL)
