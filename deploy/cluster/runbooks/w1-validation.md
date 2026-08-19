# W1 全栈验证清单(人工事项 #2、#3)

## A. 基线(每节点)
- [ ] `nvidia-smi` 正常,驱动版本与 GPU Operator 兼容矩阵一致
- [ ] `kubectl get node -o wide`:全部 Ready,K8s **v1.36.x**
- [ ] `kubectl get node -L superdl.io/pool`:池标签齐全,**kata 与 hami 无交集**
- [ ] userns GA 验证:`kubectl explain pod.spec.hostUsers` 存在;跑一个 `hostUsers: false` 测试 Pod,容器内 `readlink /proc/self/ns/user` 与宿主不同
- [ ] 内核 ≥6.3(Ubuntu 26.04):`uname -r`

## B. Kata 4.0 整卡直通实测(修正二 —— 上线前硬闸门)
1. IOMMU 分组检查(每台多卡节点):
   ```bash
   for g in /sys/kernel/iommu_groups/*/devices/*; do echo "$g"; done | grep -i nvidia
   # 每张卡应独立成组;若多卡同组 → 记录,进入预案评估
   ```
2. **8 卡节点同时开 2 个单卡 Kata 实例**:
   - 两 Pod `runtimeClassName: kata-qemu` + `nvidia.com/gpu: 1`
   - 容器内 `nvidia-smi` 各只见 1 张卡,互相无感知
3. 性能基准(损耗 <5% 为过):
   ```bash
   # 容器内:
   python -c "import torch;print(torch.cuda.is_available())"
   # 跑 3 轮 resnet50 训练吞吐 vs 裸金属基线,记录差值
   ```
4. 不过 → 预案(development-plan §1.2 修正二):
   - 预案 1:多卡节点整卡档改售「整机档」(SKU 调整)
   - 预案 2:整卡档降级 runc + userns 强化,定价与条款同步改

## C. HAMi 池(人工事项 #4,W2)
- [ ] 同卡 2 实例(各 50% 算力/8G 显存):互相 `nvidia-smi` 只见配额显存
- [ ] 互扰压测:一实例满载,另一实例吞吐衰减记录 → **定初始超卖比率的数据依据**
- [ ] 显存超限被拒:申请超过 gpumem 的分配应 OOM 在容器内,不影响邻居

## D. 存储与监控(人工事项 #5,W2)
- [ ] JuiceFS:两 Pod 挂同一 subPath 读写一致;`juicefs bench` 记录基线
- [ ] TopoLVM:PVC 创建/删除后 `lvs` 无残留;删除路径带 blkdiscard(issue_discards)
- [ ] kube-prometheus-stack:DCGM 指标可查;导入 grafana.com **24450** 大盘并按需改造
- [ ] 5 条告警规则触发测试(至少人工触发 GPUHighTemperature 或用 amtool 注入)
- [ ] Alertmanager → 平台 webhook 通路:`POST /api/v1/webhooks/alertmanager`(带 Bearer token)出现在管理端告警流
