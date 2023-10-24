# fmt:off
from typing import Optional

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from .layers.bayesian import bayesian_modules


# fmt: on
class KLDiv(nn.Module):
    """KL divergence loss for Bayesian Neural Networks. Gathers the KL from the
    modules computed in the forward passes.

    Args:
        model (nn.Module): Bayesian Neural Network
    """

    def __init__(self, model: nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self) -> Tensor:
        return self._kl_div()

    def _kl_div(self) -> Tensor:
        """Gathers pre-computed KL-Divergences from :attr:`model`."""
        kl_divergence = torch.zeros(1)
        for module in self.model.modules():
            if isinstance(module, bayesian_modules):
                kl_divergence = kl_divergence.to(
                    device=module.lvposterior.device
                )
                kl_divergence += module.lvposterior - module.lprior
        return kl_divergence


class ELBOLoss(nn.Module):
    """ELBO loss for Bayesian Neural Networks. Use this loss function with the
    objective that you seek to minimize as :attr:`criterion`.

    Args:
        model (nn.Module): The Bayesian Neural Network to compute the loss for
        criterion (nn.Module): The loss function to use during training
        kl_weight (float): The weight of the KL divergence term
        num_samples (int): The number of samples to use for the ELBO loss
    """

    def __init__(
        self,
        model: nn.Module,
        criterion: nn.Module,
        kl_weight: float,
        num_samples: int,
    ) -> None:
        super().__init__()
        self.model = model
        self._kl_div = KLDiv(model)

        if isinstance(criterion, type):
            raise ValueError(
                "The criterion should be an instance of a class."
                f"Got {criterion}."
            )
        self.criterion = criterion

        if kl_weight < 0:
            raise ValueError(
                f"The KL weight should be non-negative. Got {kl_weight}."
            )
        self.kl_weight = kl_weight

        if num_samples < 1:
            raise ValueError(
                "The number of samples should not be lower than 1."
                f"Got {num_samples}."
            )
        if not isinstance(num_samples, int):
            raise TypeError(
                "The number of samples should be an integer. "
                f"Got {type(num_samples)}."
            )
        self.num_samples = num_samples

    def forward(self, inputs: Tensor, targets: Tensor) -> Tensor:
        """Gather the kl divergence from the bayesian modules and aggregate
        the ELBO loss for a given network.

        Args:
            inputs (Tensor): The *inputs* of the Bayesian Neural Network
            targets (Tensor): The target values

        Returns:
            Tensor: The aggregated ELBO loss
        """
        aggregated_elbo = torch.zeros(1, device=inputs.device)
        for _ in range(self.num_samples):
            logits = self.model(inputs)
            aggregated_elbo += self.criterion(logits, targets)
            aggregated_elbo += self.kl_weight * self._kl_div()
        return aggregated_elbo / self.num_samples


class NIGLoss(nn.Module):
    """The Normal Inverse-Gamma loss.

    Args:
        reg_weight (float): The weight of the regularization term.
        reduction (str, optional): specifies the reduction to apply to the
        output:``'none'`` | ``'mean'`` | ``'sum'``.

    Reference:
        Amini, A., Schwarting, W., Soleimany, A., & Rus, D. (2019). Deep
        evidential regression. https://arxiv.org/abs/1910.02600.
    """

    def __init__(
        self, reg_weight: float, reduction: Optional[str] = "mean"
    ) -> None:
        super().__init__()

        if reg_weight < 0:
            raise ValueError(
                "The regularization weight should be non-negative, but got "
                f"{reg_weight}."
            )
        self.reg_weight = reg_weight
        if reduction != "none" and reduction != "mean" and reduction != "sum":
            raise ValueError(f"{reduction} is not a valid value for reduction.")
        self.reduction = reduction

    def _nig_nll(
        self,
        gamma: Tensor,
        v: Tensor,
        alpha: Tensor,
        beta: Tensor,
        targets: Tensor,
    ) -> Tensor:
        Gamma = 2 * beta * (1 + v)
        nll = (
            0.5 * torch.log(torch.pi / v)
            - alpha * Gamma.log()
            + (alpha + 0.5) * torch.log(Gamma + v * (targets - gamma) ** 2)
            + torch.lgamma(alpha)
            - torch.lgamma(alpha + 0.5)
        )
        return nll

    def _nig_reg(
        self, gamma: Tensor, v: Tensor, alpha: Tensor, targets: Tensor
    ) -> Tensor:
        reg = torch.norm(targets - gamma, 1, dim=1, keepdim=True) * (
            2 * v + alpha
        )
        return reg

    def forward(
        self,
        gamma: Tensor,
        v: Tensor,
        alpha: Tensor,
        beta: Tensor,
        targets: Tensor,
    ) -> Tensor:
        loss_nll = self._nig_nll(gamma, v, alpha, beta, targets)
        loss_reg = self._nig_reg(gamma, v, alpha, targets)
        loss = loss_nll + self.reg_weight * loss_reg

        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        else:
            return loss


class DECLoss(nn.Module):
    """The deep evidential classification loss.

    Args:
        annealing_step (int): Annealing step for the weight of the
        regularization term.
        reg_weight (float): Fixed weight of the regularization term.
        loss_type (str, optional): Specifies the loss type to apply to the
        Dirichlet parameters: ``'mse'`` | ``'log'`` | ``'digamma'``.
        reduction (str, optional): Specifies the reduction to apply to the
        output:``'none'`` | ``'mean'`` | ``'sum'``.

    Reference:
        Sensoy, M., Kaplan, L., & Kandemir, M. (2018). Evidential deep
        learning to quantify classification uncertainty.
        https://arxiv.org/abs/1806.01768.
    """

    def __init__(
        self,
        annealing_step: Optional[int] = None,
        reg_weight: Optional[float] = None,
        loss_type: str = "log",
        reduction: Optional[str] = "mean",
    ) -> None:
        super().__init__()

        if reg_weight is not None and (reg_weight < 0):
            raise ValueError(
                "The regularization weight should be non-negative, but got "
                f"{reg_weight}."
            )
        self.reg_weight = reg_weight

        if annealing_step is not None and (annealing_step <= 0):
            raise ValueError(
                "The annealing step should be positive, but got "
                f"{annealing_step}."
            )
        self.annealing_step = annealing_step

        if reduction != "none" and reduction != "mean" and reduction != "sum":
            raise ValueError(f"{reduction} is not a valid value for reduction.")
        self.reduction = reduction

        if loss_type not in ["mse", "log", "digamma"]:
            raise ValueError(
                f"{loss_type} is not a valid value for mse/log/digamma loss."
            )
        self.loss_type = loss_type

    def _mse_loss(self, evidence: Tensor, targets: Tensor) -> Tensor:
        evidence = torch.relu(evidence)
        alpha = evidence + 1.0
        strength = torch.sum(alpha, dim=1, keepdim=True)
        loglikelihood_err = torch.sum(
            (targets - (alpha / strength)) ** 2, dim=1, keepdim=True
        )
        loglikelihood_var = torch.sum(
            alpha * (strength - alpha) / (strength * strength * (strength + 1)),
            dim=1,
            keepdim=True,
        )
        loss = loglikelihood_err + loglikelihood_var
        return loss

    def _log_loss(self, evidence: Tensor, targets: Tensor) -> Tensor:
        evidence = torch.relu(evidence)
        alpha = evidence + 1.0
        strength = alpha.sum(dim=-1, keepdim=True)
        loss = torch.sum(
            targets * (torch.log(strength) - torch.log(alpha)),
            dim=1,
            keepdim=True,
        )
        return loss

    def _digamma_loss(self, evidence: Tensor, targets: Tensor) -> Tensor:
        evidence = torch.relu(evidence)
        alpha = evidence + 1.0
        strength = alpha.sum(dim=-1, keepdim=True)
        loss = torch.sum(
            targets * (torch.digamma(strength) - torch.digamma(alpha)),
            dim=1,
            keepdim=True,
        )
        return loss

    def _kldiv_reg(
        self,
        evidence: Tensor,
        targets: Tensor,
    ) -> Tensor:
        num_classes = evidence.size()[-1]
        evidence = torch.relu(evidence)
        alpha = evidence + 1.0

        kl_alpha = (alpha - 1) * (1 - targets) + 1

        ones = torch.ones(
            [1, num_classes], dtype=evidence.dtype, device=evidence.device
        )
        sum_kl_alpha = torch.sum(kl_alpha, dim=1, keepdim=True)
        first_term = (
            torch.lgamma(sum_kl_alpha)
            - torch.lgamma(kl_alpha).sum(dim=1, keepdim=True)
            + torch.lgamma(ones).sum(dim=1, keepdim=True)
            - torch.lgamma(ones.sum(dim=1, keepdim=True))
        )
        second_term = torch.sum(
            (kl_alpha - ones)
            * (torch.digamma(kl_alpha) - torch.digamma(sum_kl_alpha)),
            dim=1,
            keepdim=True,
        )
        loss = first_term + second_term
        return loss

    def forward(
        self,
        evidence: Tensor,
        targets: Tensor,
        current_epoch: Optional[int] = None,
    ) -> Tensor:
        if (
            self.annealing_step is not None
            and self.annealing_step > 0
            and current_epoch is None
        ):
            raise ValueError(
                "The epoch num should be positive when \
                annealing_step is settled, but got "
                f"{current_epoch}."
            )

        targets = F.one_hot(targets, evidence.size()[-1])
        if self.loss_type == "mse":
            loss_dirichlet = self._mse_loss(evidence, targets)
        elif self.loss_type == "log":
            loss_dirichlet = self._log_loss(evidence, targets)
        elif self.loss_type == "digamma":
            loss_dirichlet = self._digamma_loss(evidence, targets)

        if self.reg_weight is None and self.annealing_step is None:
            annealing_coef = 0
        elif (
            self.reg_weight is None
            and self.annealing_step > 0
            and current_epoch > 0
        ):
            annealing_coef = torch.min(
                torch.tensor(1.0, dtype=evidence.dtype),
                torch.tensor(
                    current_epoch / self.annealing_step, dtype=evidence.dtype
                ),
            )
        elif self.annealing_step is None and self.reg_weight > 0:
            annealing_coef = self.reg_weight
        else:
            annealing_coef = torch.min(
                torch.tensor(1.0, dtype=evidence.dtype),
                torch.tensor(
                    current_epoch / self.annealing_step, dtype=evidence.dtype
                ),
            )

        loss_reg = self._kldiv_reg(evidence, targets)

        loss = loss_dirichlet + annealing_coef * loss_reg

        if self.reduction == "mean":
            return loss.mean()
        elif self.reduction == "sum":
            return loss.sum()
        else:
            return loss
