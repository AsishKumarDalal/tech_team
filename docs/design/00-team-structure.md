# AI Tech Team — Department & Team Structure

## 1. Executive Summary

This architecture defines an autonomous software engineering organization composed of specialized AI agents. The organization operates under a hub-and-spoke model where **human oversight occurs via the Tech Lead**, and agents communicate using strict, standardized protocols.

```
                         ┌────────────────────┐
                         │     HUMAN PM       │
                         └─────────┬──────────┘
                                   │
                         ┌─────────▼──────────┐
                         │     TECH LEAD      │
                         │   (Orchestrator)   │
                         └─────────┬──────────┘
                                   │
     ┌──────────────────┬──────────┼──────────┬──────────────────┐
     │                  │          │          │                  │
┌────▼─────┐      ┌─────▼────┐ ┌───▼────┐ ┌───▼─────┐      ┌─────▼────┐
│ Product  │      │ Design   │ │  Tech  │ │ Coding  │      │ Testing  │
│ Dept     │      │ Dept     │ │ Dept   │ │ Dept    │      │ Dept     │
└──────────┘      └──────────┘ └────────┘ └─────────┘      └──────────┘
```

## 2. Department Breakdown

| Department | Primary Agent | Secondary Agents | Responsibility |
|---|---|---|---|
| **Product** | Product Manager | User Researcher | Requirements gathering, PRDs, feature definition |
| **Design** | UI/UX Designer | Design Critic | Screen flows, wireframes, visual design specs |
| **Tech Design** | System Architect | Backend Designer, DB Designer | Architecture, API specs, DB schemas |
| **Coding** | Code Lead | Sr Backend, Frontend, Junior 1, Junior 2 | Writing runnable code, file generation |
| **Testing** | QA Lead | Test Automator | Static testing, unit testing, bug reports |
