from talemate.game.engine.nodes.core import (
    Node,
    Graph,
    GraphState,
    Loop,
    Entry,
    Router,
    GraphContext,
)
from talemate.game.engine.nodes.state import SetState, GetState
from talemate.game.engine.nodes.run import (
    DefineFunction,
    GetFunction,
    CallFunction,
    FunctionReturn,
    RunModule,
)
import networkx as nx
import structlog
import pytest
from talemate.util.async_tools import cleanup_pending_tasks

log = structlog.get_logger()


class Counter(Node):
    def __init__(self, title="Counter", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_input("state")
        self.add_output("value")
        self.set_property("counter", 0)

    async def run(self, state: GraphState):
        counter = self.get_property("counter")
        self.set_output_values({"value": counter})
        self.set_property("counter", counter + 1, state)


@pytest.mark.asyncio
async def test_simple_graph():
    # Create nodes
    node_a = Node(title="A")
    node_b = Node(title="B")
    node_c = Node(title="C")
    node_d = Node(title="D")

    # Add sockets to nodes
    out_a1 = node_a.add_output("out1")
    out_a2 = node_a.add_output("out2")

    in_b = node_b.add_input("in")
    out_b = node_b.add_output("out")

    in_c = node_c.add_input("in")
    out_c = node_c.add_output("out")

    in_d1 = node_d.add_input("in1")
    in_d2 = node_d.add_input("in2")

    # Create graph
    graph = Graph()
    graph.add_node(node_a)
    graph.add_node(node_b)
    graph.add_node(node_c)
    graph.add_node(node_d)

    # Connect nodes via sockets
    graph.connect(out_a1, in_b)  # A -> B
    graph.connect(out_a2, in_c)  # A -> C
    graph.connect(out_b, in_d1)  # B -> D
    graph.connect(out_c, in_d2)  # C -> D

    nxgraph = graph.build()

    # Print paths
    print(
        [graph.node(n).title for n in nx.shortest_path(nxgraph, node_a.id, node_d.id)]
    )
    print([graph.node(n).title for n in nx.topological_sort(nxgraph)])

    # Add assertions for expected behavior
    shortest_path = [
        graph.node(n).title for n in nx.shortest_path(nxgraph, node_a.id, node_d.id)
    ]
    topo_sort = [graph.node(n).title for n in nx.topological_sort(nxgraph)]

    assert len(shortest_path) == 3, "Shortest path should have 3 nodes"
    assert shortest_path[0] == "A", "Path should start with A"
    assert shortest_path[-1] == "D", "Path should end with D"
    assert len(topo_sort) == 4, "Should have all 4 nodes in topological sort"
    assert topo_sort[0] == "A", "Topological sort should start with A"
    assert topo_sort[-1] == "D", "Topological sort should end with D"

    await cleanup_pending_tasks()


@pytest.mark.asyncio
async def test_data_flow():
    # Create nodes with specific behaviors
    class NodeA(Node):
        def __init__(self):
            super().__init__(title="A")
            self.add_output("out1")
            self.add_output("out2")

        async def run(self, state: GraphState):
            # Output constant values for testing
            self.set_output_values({"out1": 5, "out2": 10})

    class NodeB(Node):
        def __init__(self):
            super().__init__(title="B")
            self.add_input("in")
            self.add_output("out")

        async def run(self, state: GraphState):
            inputs = self.get_input_values()
            # Double the input value
            self.set_output_values({"out": inputs["in"] * 2})

    class NodeC(Node):
        def __init__(self):
            super().__init__(title="C")
            self.add_input("in")
            self.add_output("out")

        async def run(self, state: GraphState):
            inputs = self.get_input_values()
            # Add 1 to the input value
            self.set_output_values({"out": inputs["in"] + 1})

    class NodeD(Node):
        result: int = 0

        def __init__(self):
            super().__init__(title="D")
            self.add_input("in1")
            self.add_input("in2")

        async def run(self, state: GraphState):
            inputs = self.get_input_values()
            # Store sum for testing
            self.result = inputs["in1"] + inputs["in2"]

    # Create nodes
    node_a = NodeA()
    node_b = NodeB()
    node_c = NodeC()
    node_d = NodeD()

    # Create graph
    graph = Graph()
    graph.add_node(node_a)
    graph.add_node(node_b)
    graph.add_node(node_c)
    graph.add_node(node_d)

    # Connect nodes via sockets
    graph.connect(node_a.outputs[0], node_b.inputs[0])  # A.out1 -> B.in
    graph.connect(node_a.outputs[1], node_c.inputs[0])  # A.out2 -> C.in
    graph.connect(node_b.outputs[0], node_d.inputs[0])  # B.out -> D.in1
    graph.connect(node_c.outputs[0], node_d.inputs[1])  # C.out -> D.in2

    async def assert_state(state: GraphState):
        print(state.data)
        # Test data flow
        # NodeA outputs: out1=5, out2=10
        assert node_a.outputs[0].value == 5, "NodeA out1 should be 5"
        assert node_a.outputs[1].value == 10, "NodeA out2 should be 10"

        # NodeB doubles input: 5 * 2 = 10
        assert node_b.outputs[0].value == 10, "NodeB should double input value"

        # NodeC adds 1: 10 + 1 = 11
        assert node_c.outputs[0].value == 11, "NodeC should add 1 to input value"

        # NodeD sums inputs: 10 + 11 = 21
        assert node_d.result == 21, "NodeD should sum its inputs"

    # Execute graph
    graph.callbacks.append(assert_state)
    await graph.execute()
    await cleanup_pending_tasks()


@pytest.mark.asyncio
async def test_property_flow():
    # Create nodes with property-driven behaviors
    class NumberSource(Node):
        def __init__(self):
            super().__init__(title="Source")
            self.add_output("value")
            # Set default property
            self.set_property("value", 5)

        async def run(self, state: GraphState):
            # Output property value
            self.set_output_values({"value": self.get_property("value")})

    class Multiplier(Node):
        def __init__(self):
            super().__init__(title="Multiplier")
            self.add_input("value")
            self.add_output("result")
            # Set default multiplier
            self.set_property("multiplier", 2)

        async def run(self, state: GraphState):
            inputs = self.get_input_values()
            multiplier = self.get_input_value(
                "multiplier"
            )  # Will fall back to property

            print("Multiplier input:", inputs["value"], "Multiplier:", multiplier)
            self.set_output_values({"result": (inputs["value"] or 0) * multiplier})

    class Adder(Node):
        def __init__(self):
            super().__init__(title="Adder")
            self.add_input("value")
            self.add_output("result")
            # Set default addend
            self.set_property("addend", 1)

        async def run(self, state: GraphState):
            inputs = self.get_input_values()
            addend = self.get_input_value("addend")  # Will fall back to property
            self.set_output_values({"result": inputs["value"] + addend})

    class Collector(Node):
        result: float = 0

        def __init__(self):
            super().__init__(title="Collector")
            self.add_input("value1")
            self.add_input("value2")
            # Set default values
            self.set_property("value1", 0)
            self.set_property("value2", 0)

        async def run(self, state: GraphState):
            inputs = self.get_input_values()
            self.result = inputs["value1"] + inputs["value2"]

    # Create nodes and graph setup...
    source = NumberSource()
    mult = Multiplier()
    add = Adder()
    collect = Collector()

    # Create graph
    graph = Graph()
    graph.add_node(source)
    graph.add_node(mult)
    graph.add_node(add)
    graph.add_node(collect)

    # Connect nodes
    graph.connect(source.outputs[0], mult.inputs[0])  # Source -> Multiplier
    graph.connect(source.outputs[0], add.inputs[0])  # Source -> Adder
    graph.connect(mult.outputs[0], collect.inputs[0])  # Multiplier -> Collector.value1
    graph.connect(add.outputs[0], collect.inputs[1])  # Adder -> Collector.value2

    async def assert_state(state: GraphState):
        # Run assertions...
        assert source.outputs[0].value == 5, "Source should output property value"
        assert mult.outputs[0].value == 10, "Multiplier should use property multiplier"
        assert add.outputs[0].value == 6, "Adder should use property addend"
        assert collect.result == 16, "Collector should sum multiplier and adder outputs"

    # Test property defaults
    graph.callbacks.append(assert_state)
    await graph.execute()
    await cleanup_pending_tasks()


@pytest.mark.asyncio
async def test_simple_loop():
    entry_loop = Entry()
    counter = Counter()

    loop = Loop(exit_condition=lambda state: counter.get_property("counter") > 10)
    loop.add_node(entry_loop)
    loop.add_node(counter)
    loop.connect(entry_loop.outputs[0], counter.inputs[0])

    entry = Entry()
    graph = Graph()

    graph.add_node(entry)
    graph.add_node(loop)

    graph.connect(entry.outputs[0], loop.inputs[0])

    async def assert_state(state: GraphState):
        assert counter.outputs[0].value == 10, "Counter should count to 10"

    loop.callbacks.append(assert_state)
    await graph.execute()


@pytest.mark.asyncio
async def test_simple_fork():
    entry = Entry(title="Entry")
    entry_loop = Entry(title="Entry Loop")

    counter_main = Counter("CNT Main")
    counter_a = Counter("CNT A")
    counter_b = Counter("CNT B")
    router = Router(
        2,
        selector=lambda state: 0
        if counter_main.get_property("counter") % 2 == 0
        else 1,
    )

    loop = Loop(
        title="Loop",
        exit_condition=lambda state: counter_main.get_property("counter") > 10,
    )

    loop.add_node(entry_loop)
    loop.add_node(counter_main)
    loop.add_node(counter_a)
    loop.add_node(counter_b)
    loop.add_node(router)

    loop.connect(entry_loop.outputs[0], counter_main.inputs[0])
    loop.connect(counter_main.outputs[0], router.inputs[0])
    loop.connect(router.outputs[0], counter_a.inputs[0])
    loop.connect(router.outputs[1], counter_b.inputs[0])

    graph = Graph()
    graph.add_node(entry)
    graph.add_node(loop)

    graph.connect(entry.outputs[0], loop.inputs[0])

    async def assert_state_loop(state: GraphState):
        assert counter_main.get_property("counter") == 11, (
            "Main counter should count to 11"
        )
        assert counter_a.get_property("counter") == 5, "Counter A should count to 5"
        assert counter_b.get_property("counter") == 5, "Counter B should count to 5"

    loop.callbacks.append(assert_state_loop)

    await graph.execute()
    await cleanup_pending_tasks()


@pytest.mark.asyncio
async def test_visited_paths():
    """Test that nodes can be visited through multiple paths"""
    # Create a simple graph where node A connects to B both directly and through C
    # A -> B
    # A -> C -> B
    # Only one path gets deactivated, other should still work

    graph = Graph()

    # Create nodes
    node_a = Node(title="Node A")
    node_b = Node(title="Node B")
    node_c = Node(title="Node C")

    # Add nodes to graph
    graph.add_node(node_a)
    graph.add_node(node_b)
    graph.add_node(node_c)

    # Create sockets
    a_out1 = node_a.add_output("out1")
    a_out2 = node_a.add_output("out2")
    b_in1 = node_b.add_input("in1")
    b_in2 = node_b.add_input("in2")
    c_in = node_c.add_input("in")
    c_out = node_c.add_output("out")

    # Connect nodes
    # A -> B (direct path)
    graph.connect(a_out1, b_in1)
    # A -> C -> B (indirect path)
    graph.connect(a_out2, c_in)
    graph.connect(c_out, b_in2)

    with GraphContext() as state:
        # Deactivate the direct path
        a_out1.deactivated = True

        # Node A should still be available because the path through C is still active
        assert node_a.check_is_available(state), (
            "Node A should be available through path via C"
        )

        # Now deactivate the indirect path too
        a_out2.deactivated = True

        # Now Node A should be unavailable as all paths are deactivated
        assert not node_a.check_is_available(state), (
            "Node A should be unavailable when all paths are deactivated"
        )

    await cleanup_pending_tasks()


class _FreshDict(Node):
    """Outputs a brand-new dict literal every run."""

    def __init__(self, title="Fresh Dict", **kwargs):
        super().__init__(title=title, **kwargs)

    def setup(self):
        self.add_output("value")

    async def run(self, state: GraphState):
        self.set_output_values({"value": {"counter": 0}})


def _build_parent_scope_test_graph(captures: list) -> Graph:
    """
    Inner module layout (identical for both variants of the regression test):

        FreshDict -> SetState(local, "data") -> CallFunction
                                                    ^
                                                    |
                                    GetFunction -----+

        function body:
            GetState(parent, "data") -> IncrementAndCapture -> FunctionReturn
    """

    class IncrementAndCapture(Node):
        """Takes a dict, increments dict['counter'] in place, records it."""

        def __init__(self, title="Increment & Capture", **kwargs):
            super().__init__(title=title, **kwargs)

        def setup(self):
            self.add_input("value")
            self.add_output("value")

        async def run(self, state: GraphState):
            data = self.get_input_value("value")
            data["counter"] = data.get("counter", 0) + 1
            captures.append(data["counter"])
            self.set_output_values({"value": data})

    graph = Graph()

    get_parent = GetState()
    get_parent.set_property("name", "data")
    get_parent.set_property("scope", "parent")

    increment = IncrementAndCapture()
    fn_return = FunctionReturn()

    graph.add_node(get_parent)
    graph.add_node(increment)
    graph.add_node(fn_return)

    graph.connect(
        get_parent.get_output_socket("value"),
        increment.get_input_socket("value"),
    )
    graph.connect(
        increment.get_output_socket("value"),
        fn_return.get_input_socket("value"),
    )

    define_fn = DefineFunction()
    define_fn.set_property("name", "inner_fn")
    graph.add_node(define_fn)
    graph.connect(
        fn_return.get_output_socket("value"),
        define_fn.get_input_socket("nodes"),
    )

    fresh = _FreshDict()
    set_local = SetState()
    set_local.set_property("name", "data")
    set_local.set_property("scope", "local")
    get_fn = GetFunction()
    get_fn.set_property("name", "inner_fn")
    call_fn = CallFunction()

    graph.add_node(fresh)
    graph.add_node(set_local)
    graph.add_node(get_fn)
    graph.add_node(call_fn)

    graph.connect(
        fresh.get_output_socket("value"),
        set_local.get_input_socket("value"),
    )
    graph.connect(
        get_fn.get_output_socket("fn"),
        call_fn.get_input_socket("fn"),
    )
    # Ordering: SetState must run before CallFunction. Wire SetState's "value"
    # output (the dict we just stored) into CallFunction's args input. It is a
    # valid dict for args and creates a topological dependency.
    graph.connect(
        set_local.get_output_socket("value"),
        call_fn.get_input_socket("args"),
    )

    return graph


@pytest.mark.asyncio
async def test_parent_scope_state_isolation_across_runs():
    """
    Regression test: a module uses SetState(scope=local) to initialise a
    fresh mutable container, then calls an inline function whose body uses
    GetState(scope=parent) to read and mutate that container.

    Each graph execution must see a fresh container — mutations performed by
    one run must not leak into the next run.
    """

    captures: list[int] = []
    graph = _build_parent_scope_test_graph(captures)

    # Execute twice. Each run should start with counter=0, so each run's
    # capture should be 1. If parent-scope state leaks across runs, the
    # second run will see counter=1 and record 2.
    await graph.execute()
    await graph.execute()

    await cleanup_pending_tasks()

    assert captures == [1, 1], (
        f"Parent-scope state leaked across runs: captures={captures} "
        "(expected [1, 1])"
    )


@pytest.mark.asyncio
async def test_parent_scope_state_isolation_through_run_module():
    """
    Same invariant as test_parent_scope_state_isolation_across_runs, but the
    inner module is executed through a RunModule node from an outer graph -
    matching the actual production scenario reported in the bug.
    """

    captures: list[int] = []
    inner = _build_parent_scope_test_graph(captures)

    outer = Graph()

    class InnerModuleSource(Node):
        """Outputs a reference to the inner graph for RunModule to execute."""

        def __init__(self, title="Inner Module Source", **kwargs):
            super().__init__(title=title, **kwargs)

        def setup(self):
            self.add_output("module")

        async def run(self, state: GraphState):
            self.set_output_values({"module": inner})

    source = InnerModuleSource()
    run_module = RunModule()

    outer.add_node(source)
    outer.add_node(run_module)
    outer.connect(
        source.get_output_socket("module"),
        run_module.get_input_socket("module"),
    )

    # RunModule requires state.outer to be set (it uses state.outer.data
    # to detect recursive calls), so execute with an explicit outer_state.
    sentinel_outer = GraphState()
    await outer.execute(outer_state=sentinel_outer)
    await outer.execute(outer_state=sentinel_outer)

    await cleanup_pending_tasks()

    assert captures == [1, 1], (
        f"Parent-scope state leaked across runs when nested in RunModule: "
        f"captures={captures} (expected [1, 1])"
    )


@pytest.mark.asyncio
async def test_make_dict_does_not_leak_mutation_across_runs():
    """
    Regression test: data/MakeDict reads its initial value from the ``data``
    property at run time. If ``MakeDict.run`` hands out that property dict by
    reference, any downstream mutation will persist on the node across
    executions - the next run will see an already-mutated dict instead of the
    initial value.

    This was observed in the model-testing-harness test-function-calling
    graph where a MakeDict node feeds a containers dict into SetState; the
    AI-callable functions mutate that dict, and on subsequent graph runs
    the mutations from the previous run are still visible.
    """
    from talemate.game.engine.nodes.data import MakeDict

    observed: list[dict] = []

    class ObserveAndMutate(Node):
        """Records the incoming dict (deep) then mutates it in place."""

        def __init__(self, title="Observe & Mutate", **kwargs):
            super().__init__(title=title, **kwargs)

        def setup(self):
            self.add_input("value")

        async def run(self, state: GraphState):
            data = self.get_input_value("value")
            # Snapshot what we see on entry (deep copy so later mutation
            # does not change the recorded view).
            import copy

            observed.append(copy.deepcopy(data))
            # Mutate in place - this is the common case (dict is the
            # primary state container).
            data["bucket"]["apple"] = 5

    graph = Graph()
    make_dict = MakeDict()
    make_dict.set_property("data", {"bucket": {}, "basket": {}, "bowl": {}})
    observer = ObserveAndMutate()

    graph.add_node(make_dict)
    graph.add_node(observer)
    graph.connect(
        make_dict.get_output_socket("dict"),
        observer.get_input_socket("value"),
    )

    await graph.execute()
    await graph.execute()

    await cleanup_pending_tasks()

    assert observed[0] == {"bucket": {}, "basket": {}, "bowl": {}}, (
        f"First run saw an unexpected initial dict: {observed[0]}"
    )
    assert observed[1] == {"bucket": {}, "basket": {}, "bowl": {}}, (
        f"Second run saw stale mutations from the first run: {observed[1]} "
        "(MakeDict leaked a mutable reference to its data property)"
    )
