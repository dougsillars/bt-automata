# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# TODO(developer): Set your name
# Copyright © 2023 <your name>

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import torch
import torch.nn.functional as TF
from typing import Any, List

import numpy as np
from numpy.typing import NDArray

import bittensor as bt

from bt_automata.protocol import CAsynapse
from bt_automata.utils import rulesets
from bt_automata.utils.misc import decompress_and_deserialize


def get_accuracy(
    ground_truth_array: NDArray[Any],
    response: CAsynapse,
) -> float:
    """
    Returns the accuracy value (0,1) for the miner based on the comparison of the ground truth array and the response array.

    Args:
    - ground_truth_array (NDArray[Any]): The ground truth array for the cellular automata.
    - response (CAsynapse): The response from the miner.

    Returns:
    - float: The (binary) accuracy value for the miner.
    """

    try:
        pred_array = decompress_and_deserialize(response.array_data)

        if pred_array is None:
            bt.logging.debug("Failed to decompress and deserialize the response array.")
            return 0.0

        if not isinstance(pred_array, np.ndarray):
            bt.logging.debug("Response array is not a numpy array.")
            return 0.0

        accuracy = 1.0 if np.array_equal(ground_truth_array, pred_array) else 0.0

    except ValueError as e:
        bt.logging.debug(f"Error in get_accuracy: {e}")
        accuracy = 0.0

    ground_truth_str = np.array2string(ground_truth_array, threshold=10, edgeitems=2)
    pred_array_str = np.array2string(pred_array, threshold=10, edgeitems=2)

    # Log comparison
    bt.logging.info(
        f"Comparison | \nGround Truth: \n{ground_truth_str} | \nResponse: \n{pred_array_str} | \nAccuracy: {accuracy}"
    )

    return accuracy


def compute_proc_time_scores(
    process_times,
    temp = 10.,
    comp_max = 1.5,
):
    if not isinstance(process_times, torch.Tensor):
        process_times = torch.tensor(process_times)
    pt_0 = TF.normalize(process_times, dim=0)
    pt_1 = torch.hstack((pt_0, torch.tensor([comp_max])))
    pt_2 = pt_1.mean() - pt_1
    pt_3 = pt_2 / pt_2.max()
    pt_4 = temp * pt_3
    pt_5 = TF.sigmoid(pt_4)
    pt_res = pt_5[:-1]
    return pt_res


def get_rewards(
    self,
    query_synapse: CAsynapse,
    responses: List[CAsynapse],
    temp = 10.0, #Steepness of the sigmoid curve
    shift = -0.5, #Shifts sigmoid curve left or right along the x-axis
    post_norm_or_max="max", #if anything but "max" tf.normalize is used, sum of the squares in the vector == 1.
) -> torch.FloatTensor:
    if len(responses) == 0:
        bt.logging.info("Got no responses. Returning reward tensor of zeros.")
        return torch.zeros(256).to(self.device)  # Fallback strategy: Log and return 0.

    try:
        initial_state = decompress_and_deserialize(query_synapse.initial_state)
        timesteps = query_synapse.timesteps
        rule_name = query_synapse.rule_name

        if rule_name not in rulesets.rule_classes:
            bt.logging.debug(f"Unknown rule name: {rule_name}")
            return torch.FloatTensor([]).to(self.device)  # Or handle differently

        bt.logging.debug(f"Calculating rewards for {len(responses)} responses.")

        rule_func_class = rulesets.rule_classes[rule_name]
        rule_func_obj = rule_func_class()

        gt_array = rulesets.Simulate1D(
            initial_state, timesteps, rule_func_obj, r=1
        ).run()
        if gt_array is None:
            bt.logging.debug("Simulation failed to produce a result.")
            return torch.FloatTensor([]).to(self.device)  # Or handle differently

        # Pull the process times from the synapse responses
        process_times = [response.dendrite.process_time for _, response in responses]
        proc_time_scores = compute_proc_time_scores(process_times, temp=temp)

        # Calculate accuracies for each response
        accuracies = [get_accuracy(gt_array, response) for uid, response in responses]
        accuracies_tensor = torch.tensor(accuracies, dtype=torch.float32)

        # Weight the accuracy and speed, multiplying by result_accuracy to handle 0 accuracy case mathematically
        resp_uids = [uid.item() for uid, _ in responses]
        bt.logging.debug(f"\n{resp_uids=}\n{process_times=}\n{proc_time_scores=}\n{accuracies=}\n{accuracies_tensor=}")

        rewards_for_responses = accuracies_tensor * proc_time_scores
        bt.logging.debug(f"\n{rewards_for_responses=}")

        rewards = torch.zeros(256).to(self.device)
        rewards[resp_uids] = rewards_for_responses
        bt.logging.debug(f"\n{rewards=}")

        if post_norm_or_max == "max":
            rn = rewards / torch.max(rewards)
        else:
            rn = TF.normalize(rewards, dim=0) # Norm such that the sum of the squares of all the elements in the vector will be 1.
        #breakpoint()
        return rn


    except Exception as e:
        bt.logging.debug(f"Error in get_rewards: {e}")
        rewards = torch.zeros(256).to(self.device)  # Fallback strategy: Log and return 0.

    return rewards
