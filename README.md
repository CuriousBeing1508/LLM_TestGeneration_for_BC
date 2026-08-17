# LLMBBC
A repository for test generation through LLM to detect breaking changes. All execution logs, prompts, and generated tests can be found here: https://zenodo.org/records/21078853. See [DATA.md](DATA.md) for how that archive is organized.

We have packaged the tool for general purpose use at : https://github.com/CuriousBeing1508/LLMBreakGuard.git
You can run it in local or through Github Actions by forking this repository. As of now, it is configured for Claude. Will release it for GPT4o or the models evaluated in experiments. 

To test the tool in local, take a look at the bc-config.csv file where one sample instance is added to test. 

## Replication packages: BreakGuard-Class / BreakGuard-Method / BreakGuard-Minimal

These three packages are the execution pipeline used to reproduce the paper's
results for each prompting context (CLASS, METHOD, MINIMAL - i.e. how much
surrounding code the LLM was given when generating each test). Unlike the
general-purpose tool above, they start from the **execution** step: they
assume static analysis, prompt construction, and LLM prompting have already
been done, and only run the 5-phase compile/execute/merge/breaking pipeline
that turns already-generated LLM test files into pass/fail breaking-change
verdicts. See each package's own README for the expected data layout and
run instructions:

- [`BreakGuard-Class/`](BreakGuard-Class/README.md)
- [`BreakGuard-Method/`](BreakGuard-Method/README.md)
- [`BreakGuard-Minimal/`](BreakGuard-Minimal/README.md)
