#!/usr/bin/env python3
from aws_cdk import core
from microservices_cdk.microservices_cdk_stack import MicroservicesStack

app = core.App()
MicroservicesStack(app, "MicroservicesStack")
app.synth()