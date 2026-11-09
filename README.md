# movies-python

API em FastAPI (Python 3.14) com esteira de CI/CD no OpenShift usando
**OpenShift Pipelines (Tekton)** para build e **OpenShift GitOps (ArgoCD)**
para deploy.

---

## Visão geral

```
push na branch develop (este repo)
  └─ GitHub webhook ──► EventListener (movies-ci)
                          └─ Pipeline movies-python-ci
                               git-clone → pytest → buildah → update-gitops
                                                     │            │
                         quay.io/rh-ee-bcordeir/     │            └─ commit em chart/values/values-dev.yaml
                         movies-python:<sha7> ◄──────┘               (repo movies-python-devops, branch main)
                                                                          │
                                                       ArgoCD sincroniza ─┘
                                                         └─ Deployment/Service/Route "movies-python" no namespace develop
```

A imagem é construída **uma única vez**, com a tag igual ao SHA curto do
commit. O deploy é feito pelo ArgoCD a partir do repositório GitOps — a
pipeline nunca aplica nada direto nos namespaces da aplicação.

### Repositórios

| Repositório | Conteúdo |
|---|---|
| [`rh-bcordeir/movies-python`](https://github.com/rh-bcordeir/movies-python) (este) | Código, `Containerfile`, testes, manifests Tekton e ArgoCD |
| [`rh-bcordeir/movies-python-devops`](https://github.com/rh-bcordeir/movies-python-devops) | Helm chart (`chart/`) e values por ambiente (`chart/values/values-{dev,hml,prd}.yaml`) |

### Namespaces

| Namespace | Uso |
|---|---|
| `movies-ci` | Pipeline, Tasks, Triggers, secrets de CI |
| `develop` | Aplicação em dev |
| `homolog` | Aplicação em hml (ainda não criado — ver [Pendências](#pendências)) |
| `movies-prd` | Aplicação em prd (ainda não criado — ver [Pendências](#pendências)) |

### Estrutura

```
.
├── main.py                 # aplicação FastAPI
├── tests/                  # testes pytest (rodados pela pipeline)
├── requirements.txt        # gerado a partir do uv.lock, usado no build e nos testes
├── Containerfile
├── tekton/
│   ├── k8s/                # namespaces, PVC do workspace, RBAC dos triggers
│   ├── task/               # python-test, update-gitops
│   ├── pipeline/           # pipeline-ci (develop → dev)
│   └── trigger/            # webhook secret, binding, template, EventListener, Route
└── argo/
    ├── applicationset.yaml # opção A: 1 ApplicationSet gera as 3 Applications
    └── applications.yaml   # opção B: as mesmas 3 Applications declaradas à mão
```

---

## Desenvolvimento local

```sh
uv sync                              # instala dependências
uv run fastapi dev main.py           # sobe em http://127.0.0.1:8000
uv run --with pytest pytest -q       # roda os testes
```

Sempre que alterar dependências no `pyproject.toml`, regenere o
`requirements.txt` (é ele que o `Containerfile` e a Task de teste usam):

```sh
uv lock
uv export --no-hashes --no-dev --no-emit-project -o requirements.txt
```

Testar a imagem localmente:

```sh
podman build -t movies-python -f Containerfile .
podman run --rm -p 8080:8080 movies-python
```

---

## Implantação da esteira

### 1. Pré-requisitos

- Cluster OpenShift com os operators **OpenShift Pipelines** e
  **OpenShift GitOps** instalados.
- `oc` logado com permissão para criar namespaces, ClusterRoles e
  ClusterRoleBindings. `tkn` é opcional, mas ajuda a acompanhar as execuções.
- As Tasks `git-clone` e `buildah` disponíveis no namespace
  `openshift-pipelines` (a pipeline as referencia via *cluster resolver*):
  ```sh
  oc get task git-clone buildah -n openshift-pipelines
  ```
- Repositório no Quay: `quay.io/rh-ee-bcordeir/movies-python`, com um
  robot account com permissão de escrita.
- Um GitHub Personal Access Token com permissão de escrita no repositório
  `movies-python-devops` (a pipeline faz push nele).

### 2. Repositório GitOps

O repositório `movies-python-devops` precisa existir, na branch `main`, com:

```
chart/
├── Chart.yaml
├── values.yaml                 # image.repository: quay.io/rh-ee-bcordeir/movies-python
├── templates/                  # deployment, service, route (porta 8080)
└── values/
    ├── values-dev.yaml         # image.tag  ← atualizado pela pipeline
    ├── values-hml.yaml         # image.tag
    └── values-prd.yaml         # image.tag
```

A Task `update-gitops` lê e grava a chave `image.tag`, e o Deployment usa
`.Release.Name` como nome — o `releaseName: movies-python` definido no ArgoCD
é o que dá nome aos recursos no namespace.

Valide o chart localmente:

```sh
helm template movies-python chart -n develop -f chart/values/values-dev.yaml
```

### 3. Branches deste repositório

O EventListener dispara a pipeline de CI em push na branch `develop`:

```sh
git checkout -b develop
git push -u origin develop
```

### 4. Namespaces, PVC e RBAC

```sh
oc apply -f tekton/k8s/
```

Cria:
- namespaces `movies-ci` e `develop` (o `develop` com o label
  `argocd.argoproj.io/managed-by: openshift-gitops`, que dá ao ArgoCD
  permissão de deploy nele);
- PVC `pipeline-workspace` (2Gi, RWO) em `movies-ci`;
- ClusterRole/ClusterRoleBinding que permite à SA `pipeline` ler
  `clusterinterceptors` e `clustertriggerbindings` — sem isso o
  EventListener não consegue usar os interceptors `github` e `cel`.

A ServiceAccount usada em tudo é a `pipeline`, criada automaticamente pelo
operator em cada namespace. Não crie outra.

### 5. Secrets

Os secrets não ficam no Git; crie-os manualmente em `movies-ci`.

**Quay (push da imagem pelo buildah):**

```sh
oc create secret docker-registry quay-push-secret \
  --docker-server=quay.io \
  --docker-username='<robot-account>' \
  --docker-password='<token>' \
  -n movies-ci

oc secrets link pipeline quay-push-secret --for=mount -n movies-ci
```

**GitHub (push no repositório GitOps pela `update-gitops`):**

```sh
oc create secret generic git-credentials \
  --type=kubernetes.io/basic-auth \
  --from-literal=username='<github-user>' \
  --from-literal=password='<github-PAT>' \
  -n movies-ci

oc annotate secret git-credentials tekton.dev/git-0=https://github.com -n movies-ci

oc secrets link pipeline git-credentials -n movies-ci
```

A anotação `tekton.dev/git-0` faz o Tekton injetar as credenciais em todo
`git` que roda nos steps. Por isso as URLs de repositório na pipeline usam
**HTTPS**, não SSH.

### 6. Tasks e Pipeline

```sh
oc apply -f tekton/task/
oc apply -f tekton/pipeline/pipeline-ci.yaml
```

| Task | Origem | O que faz |
|---|---|---|
| `fetch-source` | `git-clone` (openshift-pipelines) | Clona este repo no commit do push |
| `test` | `python-test` (este repo) | `pip install -r requirements.txt pytest` + `pytest -q` |
| `build-image` | `buildah` (openshift-pipelines) | Build do `Containerfile` e push para o Quay com tag `<sha7>` |
| `update-gitops-dev` | `update-gitops` (este repo) | Grava `image.repository`/`image.tag` em `chart/values/values-dev.yaml` e faz push |

Os parâmetros com valor padrão da pipeline (`gitops-repo-url`,
`gitops-repo-revision`, etc.) já estão preenchidos com valores fixos em
[tekton/pipeline/pipeline-ci.yaml](tekton/pipeline/pipeline-ci.yaml). Se for
reaproveitar para outro projeto, troque-os ali — não há substituição de
variáveis em tempo de deploy.

### 7. Triggers (webhook)

> **Antes de aplicar**, troque o valor de `secretToken` em
> [tekton/trigger/trigger.yaml](tekton/trigger/trigger.yaml) por um token
> forte. O valor atual está versionado num repositório público. Gere um com
> `openssl rand -hex 20`.

```sh
oc apply -f tekton/trigger/trigger.yaml
```

Não aplique `trigger-promote.yaml` por enquanto (ver [Pendências](#pendências)).

Pegue a URL do webhook:

```sh
oc get route el-movies-python-listener -n movies-ci -o jsonpath='https://{.spec.host}{"\n"}'
```

No GitHub, em **movies-python → Settings → Webhooks → Add webhook**:

| Campo | Valor |
|---|---|
| Payload URL | a URL acima |
| Content type | `application/json` |
| Secret | o mesmo `secretToken` do `trigger.yaml` |
| Events | *Just the push event* |

Um único webhook atende todas as branches: o EventListener filtra com CEL
(`body.ref == 'refs/heads/develop'`) e só dispara a pipeline de CI para a
`develop`.

### 8. ArgoCD

Escolha **uma** das opções (os nomes das Applications são iguais nas duas;
aplicar ambas faz uma sobrescrever a outra):

**Opção A — ApplicationSet** (recomendada; adicionar ambiente = adicionar um
item na lista):

```sh
oc apply -f argo/applicationset.yaml
```

**Opção B — 3 Applications explícitas:**

```sh
oc apply -f argo/applications.yaml
```

Ambas geram `movies-python-dev`, `movies-python-hml` e `movies-python-prd` em
`openshift-gitops`, apontando para `chart/` do repositório GitOps (branch
`main`), cada uma com seu `values-<env>.yaml` e namespace de destino. O sync é
automático (`prune` + `selfHeal`).

```sh
oc get applications -n openshift-gitops -l app.kubernetes.io/part-of=movies-python
```

Se usar a opção A e nenhuma Application aparecer, confira se o controller de
ApplicationSet está rodando:
`oc get deploy -n openshift-gitops | grep applicationset`.

### 9. Primeira execução

**Manual** (útil para validar antes de configurar o webhook):

```sh
cat <<'EOF' | oc create -f -
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: movies-python-ci-manual-
  namespace: movies-ci
spec:
  pipelineRef:
    name: movies-python-ci
  taskRunTemplate:
    serviceAccountName: pipeline
  params:
    - name: app-repo-revision
      value: develop
    - name: image-tag
      value: manual-test
  workspaces:
    - name: shared-workspace
      persistentVolumeClaim:
        claimName: pipeline-workspace
EOF

tkn pipelinerun logs -L -f -n movies-ci
```

> A execução manual também atualiza o `values-dev.yaml` — o dev passará a
> rodar a tag `manual-test` até o próximo push na `develop`.

**Pelo webhook:**

```sh
git checkout develop
# ... alterações ...
git push origin develop
tkn pipelinerun list -n movies-ci
```

### 10. Verificação

```sh
# commit da pipeline no repo GitOps
git -C <clone-do-movies-python-devops> pull && cat chart/values/values-dev.yaml

# status no ArgoCD
oc get application movies-python-dev -n openshift-gitops

# aplicação
oc get pods -n develop
curl -s https://$(oc get route movies-python -n develop -o jsonpath='{.spec.host}')/
# {"Hello":"World"}
```

---

## Pendências

Itens que ainda **não** estão prontos neste repositório:

1. **Promoção dev → hml → prd.** O EventListener já tem os triggers
   `release-push` e `main-push`, mas:
   - não existe a Pipeline `movies-python-promote` nem a Task `promote-gitops`;
   - [tekton/trigger/trigger-promote.yaml](tekton/trigger/trigger-promote.yaml)
     ainda tem placeholders `{{ values_file_* }}` que precisam virar
     `chart/values/values-dev.yaml`, `values-hml.yaml` e `values-prd.yaml`.

   Até lá, pushes em `release` e `main` não promovem nada. A referência de
   implementação é a pasta `ansible/` do
   [movies-quarkus](https://github.com/rh-bcordeir/movies-quarkus/tree/main/ansible)
   (`promote-gitops` copia o `image.tag` de um values para o outro, sem
   rebuild).

2. **Namespaces de hml e prd.** As Applications `movies-python-hml` e
   `movies-python-prd` usam `CreateNamespace=false`, mas
   [tekton/k8s/namespaces.yaml](tekton/k8s/namespaces.yaml) só cria
   `movies-ci` e `develop`. Até adicionar `homolog` e `movies-prd` (com o
   label `argocd.argoproj.io/managed-by: openshift-gitops`), essas duas
   Applications ficam com erro de sync.

3. **Concorrência no workspace.** Todas as execuções usam o mesmo PVC RWO
   `pipeline-workspace`; dois pushes seguidos podem disputar o volume. Para
   isolar, troque `persistentVolumeClaim` por `volumeClaimTemplate` na
   TriggerTemplate.

---

## Troubleshooting

Problemas já encontrados ao montar esta esteira:

| Sintoma | Causa | Correção |
|---|---|---|
| Task `test` falha com `no tests ran` | pytest sai com código 5 quando não encontra testes, e a Task usa `set -e` | Manter testes em `tests/` |
| `ModuleNotFoundError: No module named 'main'` nos testes | A raiz do projeto não está no `sys.path` do pytest | `[tool.pytest.ini_options] pythonpath = ["."]` no `pyproject.toml` (já configurado) |
| buildah: `COPY ./app ... no such file or directory` | `Containerfile` copiando caminho inexistente | Copiar `main.py` (já corrigido) |
| buildah não encontra o Dockerfile | A Task `buildah` usa `./Dockerfile` por padrão | Parâmetro `DOCKERFILE: ./Containerfile` na pipeline (já configurado) |
| Pod da app não sobe / erro de bind na porta | Porta < 1024 sem root (OpenShift roda com UID aleatório) | App na porta 8080 (já configurado) |
| `fatal: repository '{{ ... }}' does not exist` | Placeholder Jinja2 copiado do movies-quarkus sem substituir | Trocar por valor fixo e reaplicar a pipeline com `oc apply` |
| `fatal: Remote branch develop not found` na `update-gitops` | `gitops-repo-revision` apontando para branch inexistente no repo **GitOps** | Usar `main` (a branch `develop` é do repo da aplicação, não do GitOps) |
| `update-gitops` falha no `git push` (403/auth) | Secret `git-credentials` sem anotação, não linkado à SA ou PAT sem escrita | Revisar o [passo 5](#5-secrets) |

Comandos úteis:

```sh
tkn pipelinerun list -n movies-ci
tkn pipelinerun describe <nome> -n movies-ci
tkn taskrun logs <pipelinerun>-<task> -n movies-ci
oc logs -l eventlistener=movies-python-listener -n movies-ci     # erros de webhook/CEL
```

### Não bloquear a pipeline se os testes falharem

Se precisar seguir com build/deploy mesmo com testes falhando, adicione
`onError: continue` ao step `pytest` em
[tekton/task/python-test.yaml](tekton/task/python-test.yaml). A TaskRun
termina como *Succeeded*, mas o step mantém o exit code real, então a falha
continua visível em `tkn taskrun describe`.

---

## Remoção

```sh
oc delete -f argo/applicationset.yaml        # ou argo/applications.yaml
oc delete -f tekton/trigger/trigger.yaml
oc delete -f tekton/k8s/
```

Como as Applications têm o finalizer `resources-finalizer.argocd.argoproj.io`,
apagá-las remove também os recursos da aplicação nos namespaces.
