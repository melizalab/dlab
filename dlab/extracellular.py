# -*- coding: utf-8 -*-
# -*- mode: python -*-
""" Utilities for extracellular experiments """
import os
import re
import json
import logging
import quickspikes as qs
import nbank
import numpy as np
import h5py as h5

from dlab import pprox

log = logging.getLogger("dlab.extracellular")

#### general

def entry_time(entry):
    from arf import timestamp_to_float
    return timestamp_to_float(entry.attrs["timestamp"])

#### present-audio


def audiolog_to_trials(trials, data_file, sync_dset="channel37", sync_thresh=1.0):
    """Parses a logfile from Margot's present_audio scripts for experiment structure

    trials: the "presentation" field in the json output of present_audio.py
    data_file: open handle to the hdf5 file generated by open-ephys during the recording
    sync_dset: the name of the dataset containing the synchronization signal
    sync_thresh: the threshold for detecting the sync signal
    trials: number of trials per stimulus
    """
    from arf import timestamp_to_float

    # Each element in this structure corresponds to a trial. In some cases the
    # data are stored as a dict/map, but the keys are just strings of the trial
    # number. The indices correspond to the entries in the arf file.
    n_trials = len(trials)
    expt_start = None
    sample_count = 0
    det = qs.detector(sync_thresh, 10)
    for i in range(n_trials):
        pproc = {"events": [], "index": i}
        pproc.update(trials[str(i)])
        entry_name = "/rec_%d" % i
        entry = data_file[entry_name]

        # get time relative to first trial
        time = entry_time(entry)
        if expt_start is None:
            expt_start = time
        pproc["offset"] = time - expt_start
        # find the sync signal - we expect one and only one click
        dset = entry[sync_dset]
        pproc["recording"] = {
            "entry": entry_name,
            "start": int(sample_count),
            "stop": int(sample_count + dset.size),
            "sampling_rate": dset.attrs["sampling_rate"],
        }
        sample_count += dset.size
        sync = dset[:].astype("d")
        det.scale_thresh(sync.mean(), sync.std())
        clicks = det(sync)
        if len(clicks) != 1:
            log.error("%s: expected 1 click, detected %d", dset.name, len(clicks))
        else:
            pproc["stim_on"] = clicks[0] / dset.attrs["sampling_rate"]
        yield pproc


def audiolog_to_pprox_script(argv=None):
    """ CLI to generate a pprox from present_audio log """
    import sys
    import argparse
    import json
    from dlab.util import setup_log, json_serializable
    __version__ = "0.1.0"

    p = argparse.ArgumentParser(
        description="generate pprox from trial structure in present_audio logfile"
    )
    p.add_argument(
        "-v", "--version", action="version", version="%(prog)s " + __version__
    )
    p.add_argument("--debug", help="show verbose log messages", action="store_true")
    p.add_argument(
        "--output",
        "-o",
        type=argparse.FileType("w", encoding="utf-8"),
        default=sys.stdout,
        help="name of output file. If absent, outputs to stdout",
    )
    p.add_argument(
        "--sync",
        default="channel37",
        help="name of channel with synchronization signal",
    )
    p.add_argument(
        "--sync-thresh",
        default="30.0",
        type=float,
        help="threshold (z-score) for detecting synchronization clicks",
    )
    p.add_argument("logfile", help="log file generated by present_audio.py")
    p.add_argument("recording", help="neurobank id or URL for the ARF recording")
    args = p.parse_args(argv)
    setup_log(log, args.debug)

    resource_url = nbank.full_url(args.recording)
    datafile = nbank.get(args.recording, local_only=True)
    if datafile is None:
        p.error(
            "unable to locate resource %s - is it deposited in neurobank?"
            % args.recording
        )
    log.info("loading recording from resource %s", resource_url)

    with h5.File(datafile, "r") as afp:
        with open(args.logfile, "rt") as lfp:
            expt_log = json.load(lfp)
            trials = pprox.from_trials(
                audiolog_to_trials(
                    expt_log.pop("presentation"), afp, args.sync, args.sync_thresh
                ),
                recording=resource_url,
                processed_by=["{} {}".format(p.prog, __version__)],
                **expt_log
            )
    json.dump(trials, args.output, default=json_serializable)
    if args.output != sys.stdout:
        log.info("wrote trial data to '%s'", args.output.name)


############### oeaudio-present:


def find_stim_dset(entry):
    """ Returns the first dataset that matches 'Network_Events.*_TEXT' """
    rex = re.compile(r"Network_Events-.*?TEXT")
    for name in entry:
        if rex.match(name) is not None:
            log.debug("  - stim log dataset: %s", name)
            return entry[name]


def parse_stim_id(path):
    """ Extracts the stimulus id from the path """
    return os.path.splitext(os.path.basename(path))[0]


def oeaudio_to_trials(data_file, sync_dset=None, sync_thresh=1.0, prepad=1.0):
    """Extracts trial information from an oeaudio-present experiment ARF file

    When using oeaudio-present, a single recording is made in response to all
    the stimuli. The stimulus presentation script sends network events to
    open-ephys to mark the start and stop of each stimulus. There is typically a
    significant lag between the 'start' event and the onset of the stimulus, due
    to buffering of the audio playback. However, the oeaudio-present script will
    play a synchronization click on a second channel by default. As long as the
    user remembers to record this channel, it can be used to correct the onset
    and offset values.

    The continuous recording is broken up into trials based on the stimulus
    presentation, such that each trial encompasses one and only one stimulus.
    The `prepad` parameter specifies, in seconds, when trials begin relative to
    stimulus onset. The default is 1.0 s.

    """
    import copy
    from arf import timestamp_to_float, timestamp_to_datetime

    re_start = re.compile(r"start (.*)")
    re_stop = re.compile(r"stop (.*)")
    expt_start = None
    index = 0
    det = qs.detector(sync_thresh, 10)
    for entry_num, entry in enumerate(sorted(data_file.values(), key=entry_time)):
        log.info("- entry: '%s'", entry.name)
        entry_start = entry_time(entry)
        log.info("  - start time: %s", timestamp_to_datetime(entry.attrs["timestamp"]))
        if expt_start is None:
            expt_start = entry_start

        if sync_dset is not None:
            sync = entry[sync_dset]
            log.info("  - sync track: '%s'", sync_dset)
            sync_data = sync[:].astype("d")
            det.scale_thresh(sync_data.mean(), sync_data.std())
            stim_onsets = np.asarray(det(sync_data))
            log.info("    - detected %d clicks", stim_onsets.size)
            dset_offset = sync.attrs["offset"]
        else:
            log.info("  - proceeding without sync track")
            # find offset in other channels:
            dset_offset = 0
            for dname, dset in entry.items():
                if "offset" in dset.attrs:
                    dset_offset = dset.attrs["offset"]
                    log.info("    - got clock offset from '%s'", dname)
                    break

        stims = find_stim_dset(entry)
        sampling_rate = stims.attrs["sampling_rate"]
        stim_sample_offset = int(dset_offset * sampling_rate)
        log.info("  - recording clock offset: %d", stim_sample_offset)
        pproc_base = {
            "events": [],
            "recording": {"entry": entry_num},
        }

        this_trial = None
        for row in stims:
            time = row["start"]
            message = row["message"].decode("utf-8")
            m = re_start.match(message)
            if m is not None:
                stim = parse_stim_id(m.group(1))
                stim_on = time - stim_sample_offset
                # adjust to next sync click
                if sync_dset is not None:
                    click_idx = stim_onsets.searchsorted(stim_on)
                    log.debug(
                        "  - trial %d: stim onset adjusted by %d",
                        index,
                        stim_onsets[click_idx] - stim_on,
                    )
                    stim_on = stim_onsets[click_idx]
                trial_on = stim_on - int(prepad * sampling_rate)
                if this_trial is not None:
                    this_trial["recording"]["stop"] = trial_on
                    index += 1
                    yield this_trial
                this_trial = copy.deepcopy(pproc_base)
                if trial_on < 0:
                    raise ValueError(
                        "start of trial %d (%012d samples) precedes recording onset - "
                        "adjust prepad" % (index, trial_on)
                    )
                this_trial.update(
                    stim=stim,
                    index=index,
                    offset=(entry_start - expt_start) + float(trial_on / sampling_rate),
                    stim_on=(stim_on - trial_on) / sampling_rate
                )
                this_trial["recording"]["start"] = trial_on
                log.debug(
                    "  - trial %d: start @ %012d samples (stim %s @ %012d)",
                    index,
                    trial_on,
                    stim,
                    stim_on,
                )
                continue
            # the stop messages are just monitored to ensure data consistency
            m = re_stop.match(message)
            if m is not None:
                stim = parse_stim_id(m.group(1))
                if this_trial is None or stim != this_trial["stim"]:
                    log.warning(
                        "  - WARNING: stop event %s without matching start event",
                        m.group(1),
                    )
                continue
            log.debug(" - skipping message at sample %d: '%s'", time, message)


def entry_metadata(data_file):
    re_metadata = re.compile(r"metadata: (\{.*\})")
    for entry_num, entry in enumerate(sorted(data_file.values(), key=entry_time)):
        stims = find_stim_dset(entry)
        for row in stims:
            time = row["start"]
            message = row["message"].decode("utf-8")
            m = re_metadata.match(message)
            try:
                metadata = json.loads(m.group(1))
            except (AttributeError, json.JSONDecodeError):
                pass
            else:
                metadata.update(name=entry.name,
                                sampling_rate=stims.attrs["sampling_rate"])
                yield metadata



def oeaudio_to_pprox_script(argv=None):
    import sys
    import argparse
    from dlab.util import setup_log, json_serializable
    __version__ = "0.1.0"

    p = argparse.ArgumentParser(
        description="generate pprox from trial structure in oeaudio-present recording"
    )
    p.add_argument(
        "-v", "--version", action="version", version="%(prog)s " + __version__
    )
    p.add_argument("--debug", help="show verbose log messages", action="store_true")
    p.add_argument(
        "--output",
        "-o",
        type=argparse.FileType("w", encoding="utf-8"),
        default=sys.stdout,
        help="name of output file. If absent, outputs to stdout",
    )
    p.add_argument(
        "--sync",
        default="sync",
        help="name of channel with synchronization signal (default %(default)s)",
    )
    p.add_argument(
        "--no-sync",
        action="store_true",
        help="determine stimulus onset without a sync track",
    )
    p.add_argument(
        "--prepad",
        type=float,
        default=1.0,
        help="sets trial start time relative to stimulus onset (default %(default)0.1f s)",
    )
    p.add_argument(
        "--sync-thresh",
        default="30.0",
        type=float,
        help="threshold (z-score) for detecting sync clicks (default %(default)0.1f)",
    )
    p.add_argument(
        "--no-neurobank",
        action="store_true",
        help="load recording file directly rather than from neurobank. For debugging only"
    )
    p.add_argument("recording", help="neurobank id or URL for the ARF recording")
    args = p.parse_args(argv)
    setup_log(log, args.debug)

    if args.no_neurobank:
        resource_url = "file://" + args.recording
        datafile = args.recording
        resource_info = {"metadata": {}}
        log.info(" - source file: '%s'", args.recording)
    else:
        resource_url = nbank.full_url(args.recording)
        resource_info = nbank.describe(resource_url)
        datafile = nbank.get(args.recording, local_only=True)
        if datafile is None:
            p.error(
                "unable to locate resource %s - is it deposited in neurobank?"
                % args.recording
            )
        log.info(" - source resource: %s", resource_url)

    if args.no_sync:
        args.sync = None
        log.warning(" - warning: not using a sync track!")

    with h5.File(datafile, "r") as afp:
        trials = pprox.from_trials(
            oeaudio_to_trials(afp, args.sync, args.sync_thresh, args.prepad),
            recording=resource_url,
            processed_by=["{} {}".format(p.prog, __version__)],
            **resource_info["metadata"]
        )
        trials["entry_metadata"] = tuple(entry_metadata(afp))

    json.dump(trials, args.output, default=json_serializable)
    if args.output != sys.stdout:
        log.info("wrote trial data to '%s'", args.output.name)