# -*- coding: utf-8 -*-
"""
Debug MIP solver
"""
import sys
sys.path.insert(0, '.')

from models.data_loader import use_real_data, get_trains_pydantic, get_stations_pydantic
from solver.mip_scheduler import MIPScheduler
from models.data_models import DelayInjection, InjectedDelay, DelayLocation, ScenarioType

use_real_data(False)
trains = get_trains_pydantic()[:2]
stations = get_stations_pydantic()

print(f"Train 0: {trains[0].train_id}")
for stop in trains[0].schedule.stops:
    print(f"  {stop.station_code}: arr={stop.arrival_time}, dep={stop.departure_time}")

scheduler = MIPScheduler(trains, stations)

# Test case: no delays
no_delay = DelayInjection(
    scenario_type=ScenarioType.TEMPORARY_SPEED_LIMIT,
    scenario_id="TEST",
    injected_delays=[],
    affected_trains=[],
    scenario_params={}
)

result = scheduler.solve(no_delay)
print(f"\nResult: success={result.success}")

# Check the actual delay values
if result.success:
    for train_id, schedule in result.optimized_schedule.items():
        print(f"\nTrain {train_id}:")
        for stop in schedule:
            orig_arr = stop['original_arrival']
            new_arr = stop['arrival_time']
            orig_dep = stop['original_departure']
            new_dep = stop['departure_time']
            delay = stop['delay_seconds']
            
            # Calculate manually
            parts1 = orig_arr.split(':')
            parts2 = new_arr.split(':')
            h1, m1 = int(parts1[0]), int(parts1[1])
            h2, m2 = int(parts2[0]), int(parts2[1])
            diff = (h2*60+m2) - (h1*60+m1)
            
            print(f"  {stop['station_code']}: orig={orig_arr}, new={new_arr}, diff={diff}min, delay={delay}")
