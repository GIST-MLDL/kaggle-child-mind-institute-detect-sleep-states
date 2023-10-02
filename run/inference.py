from pathlib import Path

import hydra
import numpy as np
import polars as pl
import torch
import torch.nn as nn
from omegaconf import DictConfig
from torch.utils.data import DataLoader
from tqdm import tqdm

from src.datamodule.seg import TestDataset
from src.feature_extractor.spectrogram import SpecFeatureExtractor
from src.models.seg.model import get_model
from src.utils.post_process import post_process_for_seg


def load_model(cfg: DictConfig) -> nn.Module:
    model = get_model(
        cfg, feature_dim=len(cfg.features), num_classes=len(cfg.labels), duration=cfg.duration
    )

    # load weights
    if cfg.weight is not None:
        weight_path = (
            Path(cfg.dir.model_dir)
            / cfg.weight["exp_name"]
            / cfg.weight["run_name"]
            / "best_model.pth"
        )
        model.load_state_dict(torch.load(weight_path))
    return model


def get_test_dataloader(cfg: DictConfig) -> DataLoader:
    """get test dataloader

    Args:
        cfg (DictConfig): config

    Returns:
        DataLoader: test dataloader
    """
    feature_dir = Path(cfg.dir.processed_dir) / cfg.phase / "chunk"
    test_dataset = TestDataset(cfg, feature_dir)
    test_dataloader = DataLoader(
        test_dataset,
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=cfg.num_workers,
        pin_memory=True,
        drop_last=False,
    )
    return test_dataloader


@hydra.main(config_path="conf", config_name="inference", version_base="1.2")
def main(cfg: DictConfig):
    test_dataloader = get_test_dataloader(cfg)
    feature_extractor = SpecFeatureExtractor(n_fft=cfg.n_fft, hop_length=cfg.hop_length)
    model = load_model(cfg)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device).eval()
    feature_extractor = feature_extractor.to(device).eval()

    preds = []
    keys = []
    for batch in tqdm(test_dataloader, desc="inference"):
        with torch.no_grad():
            with torch.cuda.amp.autocast(enabled=cfg.use_amp):
                x = batch["feature"].to(device)
                x = feature_extractor(x)
                pred = model(x)["logits"].sigmoid()
            key = batch["key"]
            preds.append(pred.detach().cpu().numpy())
            keys.extend(key)

    preds = np.concatenate(preds, axis=0)
    sub_df = post_process_for_seg(
        keys, preds[:, :, [1, 2]], score_th=cfg.post_process.score_th  # type: ignore
    )
    sub_df = sub_df.with_columns(
        (pl.col("step") - 1) * cfg.hop_length  # stepがhop_length分ずれているので修正
    )
    sub_df.write_csv(Path(cfg.dir.processed_dir) / "submission.csv")


if __name__ == "__main__":
    main()
