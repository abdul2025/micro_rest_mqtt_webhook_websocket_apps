from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_lambda as _lambda,
    aws_apigateway as apigw,
    aws_iot as iot,
    aws_iam as iam,
    aws_ecr as ecr,
    aws_ec2 as ec2,
    aws_logs as logs
)
from constructs import Construct

class MicroservicesStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # ----------------------------
        # Configuration from Context
        # ----------------------------
        cache_ttl = self.node.try_get_context("cache_ttl") or 10
        throttling_rate = self.node.try_get_context("throttling_rate") or 1000
        throttling_burst = self.node.try_get_context("throttling_burst") or 500
        lambda_memory = self.node.try_get_context("lambda_memory") or 1024
        log_level = self.node.try_get_context("log_level") or "INFO"

        # ----------------------------
        # Networking
        # ----------------------------
        vpc = ec2.Vpc(self, "MicroserviceVpc",
            max_azs=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24
                )
            ]
        )

        # ----------------------------
        # IAM Roles & Policies
        # ----------------------------
        lambda_role = iam.Role(
            self, "LambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            inline_policies={
                "api-gateway-access": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["execute-api:ManageConnections"],
                            resources=[f"arn:aws:execute-api:{self.region}:{self.account}:*/*"]
                        )
                    ]
                ),
                "iot-access": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["iot:Publish"],
                            resources=[f"arn:aws:iot:{self.region}:{self.account}:topic/*"]
                        )
                    ]
                )
            },
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSXRayDaemonWriteAccess")
            ]
        )

        # ----------------------------
        # Lambda Functions
        # ----------------------------
        def create_lambda_function(repo_name: str, function_name: str) -> _lambda.DockerImageFunction:
            return _lambda.DockerImageFunction(
                self, function_name,
                code=_lambda.DockerImageCode.from_ecr(
                    repository=ecr.Repository.from_repository_name(
                        self, f"{function_name}Repo", repo_name
                    ),
                    tag="latest"  # In production, use specific version tags
                ),
                role=lambda_role,
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                security_groups=[ec2.SecurityGroup(self, f"{function_name}SG", vpc=vpc)],
                environment={
                    "POWERTOOLS_SERVICE_NAME": function_name,
                    "LOG_LEVEL": log_level
                },
                tracing=_lambda.Tracing.ACTIVE,
                timeout=Duration.seconds(30),
                memory_size=lambda_memory,
                log_retention=logs.RetentionDays.ONE_WEEK
            )

        rest_lambda = create_lambda_function("rest-api-lambda", "RestApiLambda")
        websocket_lambda = create_lambda_function("websocket-lambda", "WebSocketLambda")
        webhook_lambda = create_lambda_function("webhook-lambda", "WebhookLambda")
        mqtt_lambda = create_lambda_function("mqtt-lambda", "MqttLambda")

        # ----------------------------
        # API Gateway Configuration
        # ----------------------------
        rest_api = apigw.LambdaRestApi(
            self, "RestApi",
            handler=rest_lambda,
            proxy=False,
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                caching_enabled=True,
                cache_ttl=Duration.seconds(cache_ttl),
                throttling_rate_limit=throttling_rate,
                throttling_burst_limit=throttling_burst,
                logging_level=apigw.MethodLoggingLevel.INFO,
                metrics_enabled=True,
                access_log_destination=apigw.LogGroupLogDestination(
                    logs.LogGroup(self, "RestApiAccessLogs")
                ),
                tracing_enabled=True
            )
        )
        rest_resource = rest_api.root.add_resource("rest")
        rest_resource.add_method("GET")

        # ----------------------------
        # WebSocket API
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
            )
        )

        apigw.WebSocketStage(
            self, "WebSocketStage",
            web_socket_api=websocket_api,
            stage_name="prod",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(
                rate_limit=throttling_rate,
                burst_limit=throttling_burst
            )
        )

        # ----------------------------
        # IoT Core with Error Handling
        # ----------------------------
        iot_republish_role = iam.Role(
            self, "IoTRepublishRole",
            assumed_by=iam.ServicePrincipal("iot.amazonaws.com"),
            inline_policies={
                "republish-policy": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            actions=["iot:Publish"],
                            resources=[f"arn:aws:iot:{self.region}:{self.account}:topic/errors/*"]
                        )
                    ]
                )
            }
        )

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
                        role_arn=iot_republish_role.role_arn,
                        topic="errors/topic"
                    )
                )
            )
        )

        # ----------------------------
        # Observability & Monitoring
        # ----------------------------
        logs.LogGroup(
            self, "LambdaLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            log_group_name=f"/aws/lambda/{self.stack_name}"
        )

        CfnOutput(
            self, "XRayTraceLink",
            value=f"https://{self.region}.console.aws.amazon.com/xray/home?region={self.region}#/traces"
        )

        # ----------------------------
        # GitHub OIDC Role (For CI/CD)
        # ----------------------------
        iam.Role(
            self, "GitHubOIDCRole",
            assumed_by=iam.OpenIdConnectPrincipal(
                iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
                    self, "GitHubOIDCProvider",
                    f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com"
                ),
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": "repo:your-org/your-repo:*"
                    }
                }
            ),
            description="Role for GitHub Actions deployments",
            role_name="GitHubActionsDeploymentRole"
        )