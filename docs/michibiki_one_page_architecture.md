flowchart TB
  subgraph GUI[GUI Layer]
    OpsView[Ops View / Tabs]
    SchedulerUI[Scheduler UI]
  end

  subgraph SVC[Services Layer]
    Facade[Facade APIs (public thin wrappers)]
    DecisionLog[Decision Logger (decisions.jsonl)]
    Runtime[Runtime State]
    Metrics[Metrics / Backtest Outputs]
    WFO[WFO + Retrain Runner]
    Stability[Stability Service\nstable/score/reasons]
    OpsHistory[Ops History\nnext_action + evidence]
    CondMine[T-43 Condition Mining\ncandidates + degradation]
    ProfitEng[T-44 Profit Engine\nupside + exit + sizing]
    AutoGov[T-45 Automation Governance\npolicy + authorize + approvals]
    Executor[Execution (Entry/Exit/Size apply)]
  end

  %% GUI calls only Facade
  OpsView --> Facade
  SchedulerUI --> Facade

  %% Data generation
  Executor --> DecisionLog
  Executor --> Runtime
  Executor --> Metrics

  %% Training & stability
  WFO --> Metrics
  WFO --> Stability

  %% Analysis layers
  DecisionLog --> CondMine
  Metrics --> CondMine
  Stability --> CondMine

  CondMine --> ProfitEng
  Stability --> ProfitEng
  Metrics --> ProfitEng

  %% Governance gate
  ProfitEng --> AutoGov
  CondMine --> AutoGov
  Stability --> AutoGov

  AutoGov -->|allow| Executor
  AutoGov -->|deny + reason| OpsHistory

  %% Ops decision summary
  Stability --> OpsHistory
  CondMine --> OpsHistory
  ProfitEng --> OpsHistory
  OpsHistory --> Facade
  Facade --> OpsView
