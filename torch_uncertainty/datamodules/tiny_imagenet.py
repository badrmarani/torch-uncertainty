from argparse import ArgumentParser
from pathlib import Path
from typing import Any, List, Optional, Union

import torchvision.transforms as T
from timm.data.auto_augment import rand_augment_transform
from torch import nn
from torch.utils.data import ConcatDataset, DataLoader
from torchvision.datasets import DTD, SVHN

from numpy.typing import ArrayLike

from ..datasets.classification import ImageNetO, TinyImageNet
from .abstract import AbstractDataModule


# fmt: on
class TinyImageNetDataModule(AbstractDataModule):
    num_classes = 200
    num_channels = 3
    training_task = "classification"

    def __init__(
        self,
        root: Union[str, Path],
        ood_detection: bool,
        batch_size: int,
        ood_ds: str = "svhn",
        rand_augment_opt: Optional[str] = None,
        num_workers: int = 1,
        pin_memory: bool = True,
        persistent_workers: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(
            root=root,
            batch_size=batch_size,
            num_workers=num_workers,
            pin_memory=pin_memory,
            persistent_workers=persistent_workers,
        )
        # TODO: COMPUTE STATS

        self.ood_detection = ood_detection
        self.ood_ds = ood_ds

        self.dataset = TinyImageNet

        if ood_ds == "imagenet-o":
            self.ood_dataset = ImageNetO
        elif ood_ds == "svhn":
            self.ood_dataset = SVHN
        elif ood_ds == "textures":
            self.ood_dataset = DTD

        if rand_augment_opt is not None:
            main_transform = rand_augment_transform(rand_augment_opt, {})
        else:
            main_transform = nn.Identity()

        self.transform_train = T.Compose(
            [
                T.RandomCrop(64, padding=4),
                T.RandomHorizontalFlip(),
                main_transform,
                T.ToTensor(),
                T.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
            ]
        )

        self.transform_test = T.Compose(
            [
                T.Resize(72),
                T.CenterCrop(64),
                T.ToTensor(),
                T.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
            ]
        )

    def _verify_splits(self, split: str) -> None:
        if split not in list(self.root.iterdir()):
            raise FileNotFoundError(
                f"a {split} TinyImagenet split was not found in {self.root},"
                f" make sure the folder contains a subfolder named {split}"
            )

    def prepare_data(self) -> None:  # coverage: ignore
        if self.ood_detection:
            if self.ood_ds != "textures":
                self.ood_dataset(
                    self.root,
                    split="test",
                    download=True,
                    transform=self.transform_test,
                )
            else:
                ConcatDataset(
                    [
                        self.ood_dataset(
                            self.root,
                            split="train",
                            download=True,
                            transform=self.transform_test,
                        ),
                        self.ood_dataset(
                            self.root,
                            split="val",
                            download=True,
                            transform=self.transform_test,
                        ),
                        self.ood_dataset(
                            self.root,
                            split="test",
                            download=True,
                            transform=self.transform_test,
                        ),
                    ]
                )

    def setup(self, stage: Optional[str] = None) -> None:
        if stage == "fit" or stage is None:
            self.train = self.dataset(
                self.root,
                split="train",
                transform=self.transform_train,
            )
            self.val = self.dataset(
                self.root,
                split="val",
                transform=self.transform_test,
            )
        if stage == "test":
            self.test = self.dataset(
                self.root,
                split="val",
                transform=self.transform_test,
            )

        if self.ood_detection:
            if self.ood_ds == "textures":
                self.ood = ConcatDataset(
                    [
                        self.ood_dataset(
                            self.root,
                            split="train",
                            download=True,
                            transform=self.transform_test,
                        ),
                        self.ood_dataset(
                            self.root,
                            split="val",
                            download=True,
                            transform=self.transform_test,
                        ),
                        self.ood_dataset(
                            self.root,
                            split="test",
                            download=True,
                            transform=self.transform_test,
                        ),
                    ]
                )
            else:
                self.ood = self.ood_dataset(
                    self.root,
                    split="test",
                    transform=self.transform_test,
                )

    def test_dataloader(self) -> List[DataLoader]:
        r"""Get test dataloaders.

        Return:
            List[DataLoader]: test set for in distribution data
            and out-of-distribution data.
        """
        dataloader = [self._data_loader(self.test)]
        if self.ood_detection:
            dataloader.append(self._data_loader(self.ood))
        return dataloader

    def _get_train_data(self) -> ArrayLike:
        return self.train.samples

    def _get_train_targets(self) -> ArrayLike:
        return self.train.label_data

    @classmethod
    def add_argparse_args(
        cls,
        parent_parser: ArgumentParser,
        **kwargs: Any,
    ) -> ArgumentParser:
        p = super().add_argparse_args(parent_parser)

        # Arguments for Tiny Imagenet
        p.add_argument(
            "--rand_augment", dest="rand_augment_opt", type=str, default=None
        )
        p.add_argument(
            "--evaluate_ood", dest="ood_detection", action="store_true"
        )
        return parent_parser
