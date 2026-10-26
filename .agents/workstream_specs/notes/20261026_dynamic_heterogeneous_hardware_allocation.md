# Note: Dynamic Heterogeneous Hardware Allocation & Multi-PC Task Routing

## Timestamp
2026-10-26T10:25:00+11:00

## User says
> "I think that 2. is something that will need more integration - with the "system configuration recommender" etc. The relative abilities of different machines are subject to change - if the laptop with 16GB of VRAM is switched off or out and about, another machine with a 1660Ti becomes the strong GPU machine (maybe - or maybe the CPU machine with Quad-channel RAM and many cores gives it a run for its money.) So /take-a-note to really grill me (and me to grill you) about the whole task for 2."

## Current context
During home network deployment planning, we discussed using `fabric_deploy.py` to orchestrate 3-4 heterogeneous PCs (e.g. high-VRAM 16GB laptop, 1660 Ti desktop, quad-channel high-RAM multi-core CPU workstation) where different nodes subscribe to specialized task queues (`runtaskworker --queues ...`). The user noted that physical topology is non-static: laptops roam, machines go to sleep, and the "strongest" node shifts dynamically. This requires a deeper architectural alignment session before codifying queue configs.

## What needs to be done
Conduct an interactive bidirectional "grill-me" architecture session to establish the design for dynamic multi-machine federation:
1. **Node Lifecycle & Heartbeats**:
   - How `WorkerHeartbeat` in `verbal_tasks` can advertise real-time machine affordances (free VRAM, CUDA compute capability, physical CPU cores, free host RAM, active model backends).
   - How tasks detect when a specialized node (e.g., 16GB laptop) drops off the LAN.
2. **Dynamic Capability Matching vs. Static Queue Strings**:
   - Evaluate moving beyond static strings (`queue_name="gpu_heavy"`) to capability requirements (e.g. `requires={"vram_mb": 12000, "cuda": True}` with fallback to `cpu_fallback=True`).
   - Interaction with `SystemConfiguration` and `hardware.py` recommender.
3. **Failover & Re-routing Semantics**:
   - What happens to pending or running tasks if a roaming node disconnects mid-task.
   - When to fall back to the 1660 Ti vs CPU quad-channel RAM.
4. **Fabric Deployment Integration**:
   - How `fabric_deploy.py` provisions host-specific roles (`reason-web`, `reason-worker`, `reason-inference`) while keeping configuration cleanly managed in versioned inventory or `.env.node.*` profiles.
5. **Bidirectional Questioning ("Grilling") Plan**:
   - Agent to question user on network trust boundaries, shared storage expectations (NFS vs local copies), and task retry tolerance.
   - User to challenge agent on runtime complexity, failure transparency in Demo UI, and avoiding over-engineering.

## Tags
deployment, fabric, hardware, task_routing, verbal_tasks, architecture, discussion, grill_me

## Status
pending
