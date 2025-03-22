from aws_cdk import (
    core,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_iot as iot,
    aws_iam as iam,
    aws_ecr as ecr,
    aws_ec2 as ec2,
    aws_logs as logs
)

class MicroservicesStack(core.Stack):
    def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # ----------------------------
        # Security Enhancements
        # ----------------------------
        vpc = ec2.Vpc(self, "MicroserviceVpc",
            max_azs=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_NAT,
                    cidr_mask=24
                )
            ]
        )

        # ----------------------------
        # IAM Role with Least Privilege
        # ----------------------------
        lambda_role = iam.Role(
            self, "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "api-gateway-access": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["execute-api:ManageConnections"],
                            resources=["*"]
                        )
                    ]
                ),
                "iot-access": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["iot:Publish"],
                            resources=["*"]
                        )
                    ]
                )
            },
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
            ]
        )

        # ----------------------------
        # Lambda Functions with Enhanced Config
        # ----------------------------
        def create_lambda_function(repo_name: str, function_name: str) -> _lambda.DockerImageFunction:
            return _lambda.DockerImageFunction(
                self, function_name,
                code=_lambda.DockerImageCode.from_ecr(
                    repository=ecr.Repository.from_repository_name(
                        self, f"{function_name}Repo", repo_name
                    ),
                    tag="latest"  # Consider using specific tags in prod
                ),
                role=lambda_role,
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_NAT),
                security_groups=[ec2.SecurityGroup(self, f"{function_name}SG", vpc=vpc)],
                environment={
                    "POWERTOOLS_SERVICE_NAME": function_name,
                    "LOG_LEVEL": "DEBUG"
                },
                tracing=_lambda.Tracing.ACTIVE,
                timeout=core.Duration.seconds(30),
                memory_size=512
            )

        rest_lambda = create_lambda_function("rest-api-lambda", "RestApiLambda")
        websocket_lambda = create_lambda_function("websocket-lambda", "WebSocketLambda")
        webhook_lambda = create_lambda_function("webhook-lambda", "WebhookLambda")
        mqtt_lambda = create_lambda_function("mqtt-lambda", "MqttLambda")

        # ----------------------------
        # API Gateway with Caching/Throttling
        # ----------------------------
        rest_api = apigw.LambdaRestApi(
            self, "RestApi",
            handler=rest_lambda,
            proxy=False,
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                caching_enabled=True,
                cache_ttl=core.Duration.seconds(
                    self.node.try_get_context("cache_ttl") or 10
                ),
                throttling_rate_limit=self.node.try_get_context("throttling_rate") or 1000,
                throttling_burst_limit=self.node.try_get_context("throttling_burst") or 500,
                logging_level=apigw.MethodLoggingLevel.INFO,
                metrics_enabled=True
            )
        )
        rest_resource = rest_api.root.add_resource("rest")
        rest_resource.add_method("GET")

        # ----------------------------
        # WebSocket API with Enhanced Monitoring
        # ----------------------------
        websocket_api = apigw.WebSocketApi(
            self, "WebSocketApi",
            connect_route_options=apigw.WebSocketRouteOptions(
                integration=apigw.WebSocketLambdaIntegration(
                    "WebSocketConnectIntegration", websocket_lambda
                )
            ),
            disconnect_route_options=apigw.WebSocketRouteOptions(
                integration=apigw.WebSocketLambdaIntegration(
                    "WebSocketDisconnectIntegration", websocket_lambda
                )
            ),
            default_route_options=apigw.WebSocketRouteOptions(
                integration=apigw.WebSocketLambdaIntegration(
                    "WebSocketDefaultIntegration", websocket_lambda
                )
            )
        )

        apigw.WebSocketStage(
            self, "WebSocketStage",
            web_socket_api=websocket_api,
            stage_name="prod",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(
                rate_limit=1000,
                burst_limit=500
            )
        )

        # ----------------------------
        # IoT Core Rule with Error Handling
        # ----------------------------
        iot.TopicRule(
            self, "IoTTopicRule",
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql="SELECT * FROM 'test/topic'",
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(
                            function_arn=mqtt_lambda.function_arn
                        )
                    )
                ],
                error_action=iot.CfnTopicRule.ActionProperty(
                    republish=iot.CfnTopicRule.RepublishActionProperty(
                        role_arn=lambda_role.role_arn,
                        topic="error/topic"
                    )
                )
            )
        )

        # ----------------------------
        # Observability
        # ----------------------------
        logs.LogGroup(
            self, "ApiGatewayAccessLogs",
            log_group_name=f"API-Gateway-Access-Logs-{self.stack_name}",
            retention=logs.RetentionDays.ONE_WEEK
        )

        core.CfnOutput(
            self, "XRayTraceLink",
            value=f"https://{self.region}.console.aws.amazon.com/xray/home?region={self.region}#/traces"
        )