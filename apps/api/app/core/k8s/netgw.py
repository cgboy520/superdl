"""Private-network gateway objects, built without calling the API. Gateway namespace: spec Secret,
Deployment, NetworkPolicy, SOCKS Service, one Service per intranet forward and (L3) the hub
headless Service. Tenant namespace: ExternalNames for the SOCKS endpoint and the intranet forwards,
one ClusterIP Service per instance forward and (L3) the headless Service naming attached Pods.
Organization gateways (L3 only) have no SOCKS or forwards; each attached instance's namespace gets
the headless Service and a NetworkPolicy admitting the gateway Pod to the attached Pods on the
hub port. Every object carries NETGW_LABEL=<uuid> (never MANAGED_LABEL: the instance reconciler
would treat it as a leaked instance endpoint). The instance side of L3 (attach Secret, init
container) is built here too and owned by the instance."""

from typing import Any

from kubernetes import client

from app.core.k8s.base import (
    CLUSTER_DOMAIN,
    INSTANCE_LABEL,
    MANAGED_LABEL,
    NETGW_ATTACH_CONTAINER,
    NETGW_CONTAINER,
    NETGW_HEALTH_PORT,
    NETGW_HUB_PORT,
    NETGW_L3_LABEL,
    NETGW_LABEL,
    NETGW_NAMESPACE,
    NETGW_NODE_LABEL,
    NETGW_SOCKS_PORT,
    NETGW_TENANT_LABEL,
    InstancePodSpec,
    NetgwAttach,
    NetgwSpec,
    netgw_attach_secret_name,
    netgw_forward_service_name,
    netgw_hub_service_name,
    netgw_l3_service_name,
    netgw_object_name,
)

NETGW_CAPABILITIES = ("NET_ADMIN", "NET_BIND_SERVICE", "MKNOD")
SPEC_HASH_ANNOTATION = "superdl.io/spec-hash"
SPEC_KEY = "spec.json"
PEERS_KEY = "peers.json"
ATTACH_KEY = "attach.json"
ATTACH_MOUNT = "/etc/netgw-attach"


def netgw_labels(spec: NetgwSpec) -> dict[str, str]:
    """An organization gateway carries no tenant label: a tenant-default policy would otherwise
    admit it to every Pod of that namespace, and its Pod labels never follow membership."""
    labels = {NETGW_LABEL: spec.uuid, "app.kubernetes.io/name": "superdl-netgw"}
    if spec.tenant_namespace is not None:
        labels[NETGW_TENANT_LABEL] = spec.tenant_namespace
    return labels


def _ns_selector(namespace: str) -> "client.V1LabelSelector":
    return client.V1LabelSelector(match_labels={"kubernetes.io/metadata.name": namespace})


def _hub_port() -> list["client.V1NetworkPolicyPort"]:
    return [client.V1NetworkPolicyPort(protocol="UDP", port=NETGW_HUB_PORT)]


def _svc_fqdn(name: str, namespace: str) -> str:
    return f"{name}.{namespace}.svc.{CLUSTER_DOMAIN}"


def netgw_security_context() -> "client.V1SecurityContext":
    """uid 0 with only NET_ADMIN / NET_BIND_SERVICE / MKNOD, read-only root, no escalation."""
    return client.V1SecurityContext(
        run_as_user=0,
        allow_privilege_escalation=False,
        read_only_root_filesystem=True,
        capabilities=client.V1Capabilities(drop=["ALL"], add=list(NETGW_CAPABILITIES)),
        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
    )


def build_netgw_secret(spec: NetgwSpec) -> "client.V1Secret":
    return client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=netgw_object_name(spec.uuid), namespace=NETGW_NAMESPACE, labels=netgw_labels(spec)
        ),
        string_data={SPEC_KEY: spec.spec_json, PEERS_KEY: spec.peers_json},
    )


def _container(spec: NetgwSpec) -> "client.V1Container":
    def probe(path: str, **kw: int) -> "client.V1Probe":
        return client.V1Probe(
            http_get=client.V1HTTPGetAction(path=path, port=NETGW_HEALTH_PORT), **kw
        )

    ports = [
        client.V1ContainerPort(container_port=NETGW_SOCKS_PORT, name="socks"),
        client.V1ContainerPort(container_port=NETGW_HEALTH_PORT, name="health"),
    ]
    if spec.l3:
        ports.append(
            client.V1ContainerPort(container_port=NETGW_HUB_PORT, name="hub", protocol="UDP")
        )
    return client.V1Container(
        name=NETGW_CONTAINER,
        image=spec.image,
        args=[f"/etc/netgw/{SPEC_KEY}"],
        ports=ports,
        resources=client.V1ResourceRequirements(
            requests={"cpu": "50m", "memory": "64Mi", "ephemeral-storage": "16Mi"},
            limits={"cpu": "1", "memory": "256Mi", "ephemeral-storage": "64Mi"},
        ),
        security_context=netgw_security_context(),
        readiness_probe=probe("/healthz", period_seconds=5, timeout_seconds=2, failure_threshold=2),
        liveness_probe=probe(
            "/livez",
            initial_delay_seconds=10,
            period_seconds=20,
            timeout_seconds=3,
            failure_threshold=3,
        ),
        volume_mounts=[
            client.V1VolumeMount(name="spec", mount_path="/etc/netgw", read_only=True),
            client.V1VolumeMount(name="run", mount_path="/run"),
            client.V1VolumeMount(name="tmp", mount_path="/tmp"),
        ],
    )


def build_netgw_deployment(spec: NetgwSpec) -> "client.V1Deployment":
    """One replica, Recreate (two Pods must never dial the same peer); the spec hash annotation
    rolls the Pod on every change; resolver is the agent's dnsmasq on 127.0.0.1."""
    labels = netgw_labels(spec)
    annotations = {SPEC_HASH_ANNOTATION: spec.spec_hash}
    if spec.bandwidth_mbps:
        annotations["kubernetes.io/egress-bandwidth"] = f"{spec.bandwidth_mbps}M"
    pod = client.V1PodSpec(
        containers=[_container(spec)],
        automount_service_account_token=False,
        enable_service_links=False,
        dns_policy="None",
        dns_config=client.V1PodDNSConfig(
            nameservers=["127.0.0.1"],
            options=[client.V1PodDNSConfigOption(name="ndots", value="1")],
        ),
        node_selector={NETGW_NODE_LABEL: "true"},
        termination_grace_period_seconds=20,
        image_pull_secrets=(
            [client.V1LocalObjectReference(name=spec.image_pull_secret)]
            if spec.image_pull_secret
            else None
        ),
        volumes=[
            client.V1Volume(
                name="spec",
                secret=client.V1SecretVolumeSource(
                    secret_name=netgw_object_name(spec.uuid), default_mode=0o400
                ),
            ),
            client.V1Volume(
                name="run",
                empty_dir=client.V1EmptyDirVolumeSource(medium="Memory", size_limit="16Mi"),
            ),
            client.V1Volume(name="tmp", empty_dir=client.V1EmptyDirVolumeSource(size_limit="16Mi")),
        ],
    )
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=netgw_object_name(spec.uuid), namespace=NETGW_NAMESPACE, labels=labels
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            revision_history_limit=1,
            strategy=client.V1DeploymentStrategy(type="Recreate"),
            selector=client.V1LabelSelector(match_labels={NETGW_LABEL: spec.uuid}),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=labels, annotations=annotations), spec=pod
            ),
        ),
    )


def _tenant_rules(
    spec: NetgwSpec,
) -> tuple[list["client.V1NetworkPolicyIngressRule"], list["client.V1NetworkPolicyEgressRule"]]:
    """Personal: the tenant's whole namespace, every port (forwards, SOCKS, instance forwards).
    Organization: the attached Pods of the listed namespaces on the hub port only; no rule at all
    without attachments (an empty `In` list is invalid)."""
    if spec.tenant_namespace is not None:
        tenant = client.V1NetworkPolicyPeer(namespace_selector=_ns_selector(spec.tenant_namespace))
        return (
            [client.V1NetworkPolicyIngressRule(_from=[tenant])],
            [client.V1NetworkPolicyEgressRule(to=[tenant])],
        )
    if not spec.l3_namespaces:
        return [], []
    attached = client.V1NetworkPolicyPeer(
        namespace_selector=client.V1LabelSelector(
            match_expressions=[
                client.V1LabelSelectorRequirement(
                    key="kubernetes.io/metadata.name",
                    operator="In",
                    values=list(spec.l3_namespaces),
                )
            ]
        ),
        pod_selector=client.V1LabelSelector(match_labels={NETGW_L3_LABEL: spec.uuid}),
    )
    return (
        [client.V1NetworkPolicyIngressRule(_from=[attached], ports=_hub_port())],
        [client.V1NetworkPolicyEgressRule(to=[attached], ports=_hub_port())],
    )


def build_netgw_netpol(spec: NetgwSpec, *, except_cidrs: list[str]) -> "client.V1NetworkPolicy":
    """Ingress: the tenant side (`_tenant_rules`). Egress: cluster DNS, the peer ports to the
    internet (private ranges excepted, so a peer must be public), the tenant side."""
    ingress, tenant_egress = _tenant_rules(spec)
    return client.V1NetworkPolicy(
        metadata=client.V1ObjectMeta(
            name=netgw_object_name(spec.uuid), namespace=NETGW_NAMESPACE, labels=netgw_labels(spec)
        ),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(match_labels={NETGW_LABEL: spec.uuid}),
            policy_types=["Ingress", "Egress"],
            ingress=ingress,
            egress=[
                client.V1NetworkPolicyEgressRule(
                    to=[
                        client.V1NetworkPolicyPeer(
                            namespace_selector=_ns_selector("kube-system"),
                            pod_selector=client.V1LabelSelector(
                                match_labels={"k8s-app": "kube-dns"}
                            ),
                        )
                    ],
                    ports=[
                        client.V1NetworkPolicyPort(protocol="UDP", port=53),
                        client.V1NetworkPolicyPort(protocol="TCP", port=53),
                    ],
                ),
                client.V1NetworkPolicyEgressRule(
                    to=[
                        client.V1NetworkPolicyPeer(
                            ip_block=client.V1IPBlock(cidr="0.0.0.0/0", _except=except_cidrs)
                        )
                    ],
                    ports=[
                        client.V1NetworkPolicyPort(protocol=proto.upper(), port=port)
                        for proto, port in spec.egress_ports
                    ],
                ),
                *tenant_egress,
            ],
        ),
    )


def build_netgw_tenant_netpol(spec: NetgwSpec, namespace: str) -> "client.V1NetworkPolicy":
    """Organization gateway, one per attached instance's namespace: the attached Pods and the
    gateway Pod reach each other on the hub port (adds to tenant-default, which stays as is)."""
    gateway = client.V1NetworkPolicyPeer(
        namespace_selector=_ns_selector(NETGW_NAMESPACE),
        pod_selector=client.V1LabelSelector(match_labels={NETGW_LABEL: spec.uuid}),
    )
    return client.V1NetworkPolicy(
        metadata=client.V1ObjectMeta(
            name=netgw_object_name(spec.uuid), namespace=namespace, labels={NETGW_LABEL: spec.uuid}
        ),
        spec=client.V1NetworkPolicySpec(
            pod_selector=client.V1LabelSelector(match_labels={NETGW_L3_LABEL: spec.uuid}),
            policy_types=["Ingress", "Egress"],
            ingress=[client.V1NetworkPolicyIngressRule(_from=[gateway], ports=_hub_port())],
            egress=[client.V1NetworkPolicyEgressRule(to=[gateway], ports=_hub_port())],
        ),
    )


def _service(
    name: str,
    namespace: str,
    labels: dict[str, str],
    *,
    selector: dict[str, str] | None = None,
    port: int | None = None,
    target_port: int | None = None,
    external_name: str | None = None,
    headless_udp: int | None = None,
) -> "client.V1Service":
    if external_name is not None:
        svc_spec = client.V1ServiceSpec(type="ExternalName", external_name=external_name)
    elif headless_udp is not None:
        svc_spec = client.V1ServiceSpec(
            type="ClusterIP",
            cluster_ip="None",
            publish_not_ready_addresses=True,
            selector=selector,
            ports=[
                client.V1ServicePort(
                    name="hub", protocol="UDP", port=headless_udp, target_port=headless_udp
                )
            ],
        )
    else:
        svc_spec = client.V1ServiceSpec(
            type="ClusterIP",
            selector=selector,
            ports=[
                client.V1ServicePort(name="tcp", protocol="TCP", port=port, target_port=target_port)
            ],
        )
    return client.V1Service(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace, labels=labels),
        spec=svc_spec,
    )


def build_netgw_services(spec: NetgwSpec) -> list["client.V1Service"]:
    """Gateway namespace: SOCKS Service + one Service per intranet forward (own ClusterIP, so two
    forwards may share a port number) + the hub headless Service (L3; resolves even while the
    tunnel is down)."""
    labels = netgw_labels(spec)
    selector = {NETGW_LABEL: spec.uuid}
    out = []
    if not spec.org:
        out.append(
            _service(
                netgw_object_name(spec.uuid),
                NETGW_NAMESPACE,
                labels,
                selector=selector,
                port=NETGW_SOCKS_PORT,
                target_port=NETGW_SOCKS_PORT,
            )
        )
    out.extend(
        _service(
            netgw_forward_service_name(spec.uuid, f.forward_id),
            NETGW_NAMESPACE,
            labels,
            selector=selector,
            port=f.port,
            target_port=f.listen_port,
        )
        for f in spec.intranet
    )
    if spec.l3:
        out.append(
            _service(
                netgw_hub_service_name(spec.uuid),
                NETGW_NAMESPACE,
                labels,
                selector=selector,
                headless_udp=NETGW_HUB_PORT,
            )
        )
    return out


def build_l3_service(spec: NetgwSpec, namespace: str) -> "client.V1Service":
    """Headless Service naming the gateway's attached Pods of one namespace (their `subdomain`)."""
    return _service(
        netgw_l3_service_name(spec.uuid),
        namespace,
        {NETGW_LABEL: spec.uuid},
        selector={NETGW_L3_LABEL: spec.uuid},
        headless_udp=NETGW_HUB_PORT,
    )


def build_tenant_services(spec: NetgwSpec) -> list["client.V1Service"]:
    """Personal gateway, tenant namespace: ExternalNames (SOCKS under the gateway name, intranet
    forwards under their names), the instance-forward Services and (L3) the headless Service of the
    attached Pods."""
    labels = {NETGW_LABEL: spec.uuid}
    ns = spec.tenant_namespace
    assert ns is not None
    out = [
        _service(
            spec.socks_name,
            ns,
            labels,
            external_name=_svc_fqdn(netgw_object_name(spec.uuid), NETGW_NAMESPACE),
        )
    ]
    out.extend(
        _service(
            f.name,
            ns,
            labels,
            external_name=_svc_fqdn(
                netgw_forward_service_name(spec.uuid, f.forward_id), NETGW_NAMESPACE
            ),
        )
        for f in spec.intranet
    )
    out.extend(
        _service(
            netgw_forward_service_name(spec.uuid, f.forward_id),
            ns,
            labels,
            selector={INSTANCE_LABEL: f.instance_name},
            port=f.port,
            target_port=f.port,
        )
        for f in spec.instance
    )
    if spec.l3:
        out.append(build_l3_service(spec, ns))
    return out


def service_shape(svc: Any) -> tuple[object, ...]:
    """Comparable shape of a Service (type, headless, ExternalName, selector, ports); a differing
    shape is recreated rather than patched (merge-patching ports keeps stale entries, clusterIP is
    immutable)."""
    s = svc.spec
    ports = tuple(
        (p.port, int(p.target_port) if p.target_port is not None else None, p.protocol or "TCP")
        for p in (s.ports or [])
    )
    return (
        s.type or "ClusterIP",
        s.cluster_ip == "None",
        bool(s.publish_not_ready_addresses),
        s.external_name,
        tuple(sorted((s.selector or {}).items())),
        ports,
    )


def build_attach_secret(spec: InstancePodSpec, attach: NetgwAttach) -> "client.V1Secret":
    """Owned by the instance (deleted with it, re-rendered on every start)."""
    return client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=netgw_attach_secret_name(spec.name),
            namespace=spec.namespace,
            labels={INSTANCE_LABEL: spec.name, MANAGED_LABEL: "true"},
        ),
        string_data={ATTACH_KEY: attach.config_json},
    )


def attach_init_container(attach: NetgwAttach) -> "client.V1Container":
    """Runs once before the workspace: uid 0 of the Pod's user namespace with NET_ADMIN only (it
    reaches nothing but the Pod's own netns), read-only root, the attach Secret its only mount."""
    return client.V1Container(
        name=NETGW_ATTACH_CONTAINER,
        image=attach.image,
        command=["python3", "-m", "netgw_agent.attach"],
        args=[f"{ATTACH_MOUNT}/{ATTACH_KEY}"],
        resources=client.V1ResourceRequirements(
            requests={"cpu": "10m", "memory": "32Mi", "ephemeral-storage": "1Mi"},
            limits={"cpu": "500m", "memory": "64Mi", "ephemeral-storage": "16Mi"},
        ),
        security_context=client.V1SecurityContext(
            run_as_user=0,
            allow_privilege_escalation=False,
            read_only_root_filesystem=True,
            capabilities=client.V1Capabilities(drop=["ALL"], add=["NET_ADMIN"]),
            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
        ),
        volume_mounts=[
            client.V1VolumeMount(
                name=NETGW_ATTACH_CONTAINER, mount_path=ATTACH_MOUNT, read_only=True
            )
        ],
    )


def attach_volume(instance_name: str) -> "client.V1Volume":
    return client.V1Volume(
        name=NETGW_ATTACH_CONTAINER,
        secret=client.V1SecretVolumeSource(
            secret_name=netgw_attach_secret_name(instance_name), default_mode=0o400
        ),
    )
